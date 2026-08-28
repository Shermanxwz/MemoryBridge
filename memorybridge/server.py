from __future__ import annotations

import hmac

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

from .config import Settings
from .models import Ack, MemoryPut
from .service import MemoryService


class StaticTokenVerifier(TokenVerifier):
    def __init__(self, tokens: tuple[str, ...]) -> None:
        self.tokens = tokens

    async def verify_token(self, token: str) -> AccessToken | None:
        for idx, expected in enumerate(self.tokens):
            if hmac.compare_digest(token, expected):
                return AccessToken(token=token, client_id=f"memorybridge-device-{idx+1}", scopes=["memory"])
        return None


def build_server(settings: Settings | None = None) -> FastMCP:
    settings = settings or Settings()
    kwargs = {
        "name": "MemoryBridge",
        "instructions": (
            "Portable memory source. Prefer the host agent's native memory index when available. "
            "Use memory_search only as fallback retrieval. Writes are durable in Qdrant before indexing."
        ),
        "stateless_http": True,
        "json_response": True,
        "host": settings.host,
        "port": settings.port,
    }
    if settings.bearer_tokens:
        kwargs["token_verifier"] = StaticTokenVerifier(settings.bearer_tokens)
        kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(settings.auth_issuer),
            resource_server_url=AnyHttpUrl(settings.public_mcp_url),
            required_scopes=["memory"],
        )
    mcp = FastMCP(**kwargs)
    service = MemoryService(settings)

    @mcp.tool()
    async def memory_put(
        content: str,
        source_agent: str = "unknown",
        source_device: str | None = None,
        session_id: str | None = None,
        role: str = "memory",
        project: str | None = None,
        metadata: dict | None = None,
        idempotency_key: str | None = None,
        created_at: str | None = None,
    ) -> dict:
        """Durably append one memory. Vector indexing is asynchronous and never blocks storage."""
        put = MemoryPut(
            content=content,
            source_agent=source_agent,
            source_device=source_device,
            session_id=session_id,
            role=role,  # validated by Pydantic
            project=project,
            metadata=metadata or {},
            idempotency_key=idempotency_key,
            created_at=created_at,
        )
        return await service.put(put)

    @mcp.tool()
    async def memory_scan(limit: int = 100, cursor: str | None = None) -> dict:
        """Page raw memories for first-time/native-index import. Returns an opaque next_cursor."""
        return await service.scan(limit=max(1, min(limit, 500)), cursor=cursor)

    @mcp.tool()
    async def memory_since(cursor: int, limit: int = 100) -> dict:
        """Incrementally fetch MemoryBridge-owned writes after a monotonically increasing cursor."""
        return await service.since(cursor, limit=max(1, min(limit, 500)))

    @mcp.tool()
    async def memory_get(memory_id: str, collection: str | None = None) -> dict | None:
        """Fetch one raw memory without forcing any retrieval strategy."""
        return await service.get(memory_id, collection)

    @mcp.tool()
    async def memory_recent(limit: int = 50) -> list[dict]:
        """Last-resort raw/recent access; requires no model or vector index."""
        return await service.recent(limit=max(1, min(limit, 200)))

    @mcp.tool()
    async def memory_ack(consumer: str, cursor: int, status: str = "indexed") -> dict:
        """A consumer acknowledges how far it delivered/indexed the central stream."""
        return await service.ack(Ack(consumer=consumer, cursor=cursor, status=status))

    @mcp.tool()
    async def memory_status() -> dict:
        """Health, collection counts, head cursor, consumer lag and fallback capability."""
        return await service.status()

    @mcp.tool()
    async def memory_search(query: str, limit: int = 12) -> dict:
        """Fallback only: vector via local embedding/Qdrant, then lexical, then raw recent."""
        result = await service.search(query, limit=limit)
        return result.model_dump()

    mcp._memorybridge_service = service  # type: ignore[attr-defined]
    return mcp


def main() -> None:
    settings = Settings()
    mcp = build_server(settings)
    # FastMCP's production Streamable HTTP transport serves /mcp.
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
