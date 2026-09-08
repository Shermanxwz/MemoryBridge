from __future__ import annotations

import hmac
from collections.abc import Iterable
from typing import Any

import httpx
from mcp.server.auth.provider import AccessToken, TokenVerifier


class StaticTokenVerifier(TokenVerifier):
    """Verify operator-managed opaque bearer tokens."""

    def __init__(self, tokens: tuple[str, ...], scopes: tuple[str, ...] = ("memory",)) -> None:
        self.tokens = tokens
        self.scopes = list(scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        for idx, expected in enumerate(self.tokens):
            if hmac.compare_digest(token, expected):
                return AccessToken(
                    token=token,
                    client_id=f"memorybridge-device-{idx + 1}",
                    scopes=self.scopes,
                )
        return None


class IntrospectionTokenVerifier(TokenVerifier):
    """Verify OAuth 2.1 bearer tokens with an RFC 7662 introspection endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        client_id: str = "",
        client_secret: str = "",
        resource_server_url: str = "",
        expected_issuer: str = "",
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.client_id = client_id
        self.client_secret = client_secret
        self.resource_server_url = resource_server_url.rstrip("/")
        self.expected_issuer = expected_issuer.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    @staticmethod
    def _scopes(value: Any) -> list[str]:
        if isinstance(value, str):
            return [part for part in value.split() if part]
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
            return [str(part) for part in value if str(part)]
        return []

    def _resource_from_payload(self, payload: dict[str, Any]) -> str | None:
        """Validate the RFC 8707 resource/audience bound to an access token.

        OAuth mode is deliberately resource-bound: the introspection response must
        advertise an RFC 8707 resource (or audience) matching this MCP endpoint.
        Tokens minted for another resource, or with no binding, are rejected.
        """
        if not self.resource_server_url:
            return None
        raw = payload.get("resource", payload.get("aud"))
        if raw is None:
            return ""
        if isinstance(raw, str):
            candidates = [raw]
        elif isinstance(raw, Iterable) and not isinstance(raw, (str, bytes, dict)):
            candidates = [str(item) for item in raw]
        else:
            return ""
        normalized = {candidate.rstrip("/") for candidate in candidates}
        return self.resource_server_url if self.resource_server_url in normalized else ""

    async def verify_token(self, token: str) -> AccessToken | None:
        data: dict[str, str] = {"token": token}
        auth: httpx.BasicAuth | None = None
        if self.client_id and self.client_secret:
            auth = httpx.BasicAuth(self.client_id, self.client_secret)
        elif self.client_id:
            data["client_id"] = self.client_id

        try:
            response = await self._client.post(
                self.endpoint,
                data=data,
                auth=auth,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        if not isinstance(payload, dict) or payload.get("active") is not True:
            return None

        issuer = payload.get("iss")
        if self.expected_issuer:
            if issuer is None or str(issuer).rstrip("/") != self.expected_issuer:
                return None

        resource = self._resource_from_payload(payload)
        if resource == "":
            return None

        expires_at: int | None = None
        raw_exp = payload.get("exp")
        if isinstance(raw_exp, int | float):
            expires_at = int(raw_exp)

        client_id = str(
            payload.get("client_id") or payload.get("azp") or payload.get("sub") or "oauth-client"
        )
        subject = payload.get("sub")
        claims = {key: payload[key] for key in ("iss", "aud", "username") if key in payload}
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=self._scopes(payload.get("scope", payload.get("scopes"))),
            expires_at=expires_at,
            resource=resource,
            subject=str(subject) if subject is not None else None,
            claims=claims or None,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def build_token_verifier(settings):
    """Build exactly one server-side bearer verifier from Settings."""
    if settings.oauth_introspection_url and settings.bearer_tokens:
        raise ValueError(
            "configure either MEMORYBRIDGE_BEARER_TOKENS or "
            "MEMORYBRIDGE_OAUTH_INTROSPECTION_URL, not both"
        )
    if settings.oauth_introspection_url:
        return IntrospectionTokenVerifier(
            settings.oauth_introspection_url,
            client_id=settings.oauth_introspection_client_id,
            client_secret=settings.oauth_introspection_client_secret,
            resource_server_url=settings.public_mcp_url,
            expected_issuer=settings.auth_issuer,
            timeout=float(settings.oauth_introspection_timeout),
        )
    if settings.bearer_tokens:
        return StaticTokenVerifier(settings.bearer_tokens, settings.auth_required_scopes)
    return None
