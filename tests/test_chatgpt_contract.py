import asyncio

import httpx
import pytest

from memorybridge import auth
from memorybridge.config import Settings
from memorybridge.server import build_server


EXPECTED_TOOLS = {
    "memory_put",
    "memory_scan",
    "memory_since",
    "memory_get",
    "memory_recent",
    "memory_ack",
    "memory_status",
    "memory_search",
}
READ_ONLY_TOOLS = EXPECTED_TOOLS - {"memory_put", "memory_ack"}


def test_chatgpt_scan_tools_contract_has_complete_safety_annotations():
    server = build_server(Settings())
    try:
        tools = asyncio.run(server.list_tools())
        by_name = {tool.name: tool for tool in tools}
        assert set(by_name) == EXPECTED_TOOLS

        for name in READ_ONLY_TOOLS:
            annotations = by_name[name].annotations
            assert annotations is not None
            assert annotations.read_only_hint is True
            assert annotations.idempotent_hint is True
            assert annotations.open_world_hint is False

        put = by_name["memory_put"].annotations
        assert put is not None
        assert put.read_only_hint is False
        assert put.destructive_hint is False
        assert put.idempotent_hint is False
        assert put.open_world_hint is False

        ack = by_name["memory_ack"].annotations
        assert ack is not None
        assert ack.read_only_hint is False
        assert ack.destructive_hint is False
        assert ack.idempotent_hint is True
        assert ack.open_world_hint is False
    finally:
        asyncio.run(server._memorybridge_service.close())


@pytest.mark.asyncio
async def test_oauth_introspection_verifier_accepts_active_scoped_token_and_checks_resource():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/introspect"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "active": True,
                "client_id": "chatgpt",
                "sub": "user-123",
                "scope": "memory offline_access",
                "exp": 4_102_444_800,
                "iss": "https://auth.example.com",
                "aud": ["https://memory.example.com/mcp"],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = auth.IntrospectionTokenVerifier(
        "https://auth.example.com/introspect",
        client_id="memorybridge",
        client_secret="secret",
        resource_server_url="https://memory.example.com/mcp",
        expected_issuer="https://auth.example.com",
        client=client,
    )
    try:
        token = await verifier.verify_token("opaque-access-token")
        assert token is not None
        assert token.client_id == "chatgpt"
        assert token.subject == "user-123"
        assert token.scopes == ["memory", "offline_access"]
        assert token.resource == "https://memory.example.com/mcp"
        assert token.claims == {
            "iss": "https://auth.example.com",
            "aud": ["https://memory.example.com/mcp"],
        }
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_oauth_introspection_verifier_rejects_wrong_audience():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"active": True, "scope": "memory", "aud": "https://other.example.com/mcp"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = auth.IntrospectionTokenVerifier(
        "https://auth.example.com/introspect",
        resource_server_url="https://memory.example.com/mcp",
        client=client,
    )
    try:
        assert await verifier.verify_token("wrong-audience") is None
    finally:
        await client.aclose()


def test_auth_modes_are_mutually_exclusive():
    settings = Settings(
        bearer_tokens=("static-token",),
        oauth_introspection_url="https://auth.example.com/introspect",
    )
    with pytest.raises(ValueError, match="either MEMORYBRIDGE_BEARER_TOKENS"):
        auth.build_token_verifier(settings)


def test_static_token_verifier_preserves_existing_agent_auth_contract():
    verifier = auth.StaticTokenVerifier(("alpha", "beta"), ("memory",))
    accepted = asyncio.run(verifier.verify_token("beta"))
    rejected = asyncio.run(verifier.verify_token("gamma"))
    assert accepted is not None
    assert accepted.scopes == ["memory"]
    assert rejected is None


@pytest.mark.asyncio
async def test_oauth_introspection_requires_resource_binding():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"active": True, "scope": "memory"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = auth.IntrospectionTokenVerifier(
        "https://auth.example.com/introspect",
        resource_server_url="https://memory.example.com/mcp",
        client=client,
    )
    try:
        assert await verifier.verify_token("unbound-token") is None
    finally:
        await client.aclose()
