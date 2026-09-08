from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from .auth import IntrospectionTokenVerifier, build_token_verifier
from .config import Settings
from .models import Ack, MemoryPut
from .service import MemoryService


def transport_security(settings: Settings) -> TransportSecuritySettings:
    """Allow the bound host and the configured reverse-proxy host only."""
    public = urlparse(settings.public_mcp_url)
    hosts = {
        "localhost",
        "127.0.0.1",
        settings.host,
        f"{settings.host}:{settings.port}",
    }
    if public.hostname:
        hosts.add(public.hostname)
    if public.netloc:
        hosts.add(public.netloc)
    return TransportSecuritySettings(allowed_hosts=sorted(hosts))


def build_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or Settings()
    service = MemoryService(settings)
    token_verifier = build_token_verifier(settings)

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            if isinstance(token_verifier, IntrospectionTokenVerifier):
                await token_verifier.close()
            await service.close()

    kwargs = {
        "name": "MemoryBridge",
        "instructions": (
            "Portable durable memory source for ChatGPT, Codex and other MCP clients. "
            "Prefer a host agent's native memory index when it exists. Use memory_search for prior durable context. "
            "Use memory_put only for information worth retaining beyond the current conversation or task. "
            "Never treat MCP connectivity alone as permission to capture every conversation."
        ),
        "lifespan": lifespan,
    }
    if token_verifier is not None:
        kwargs["token_verifier"] = token_verifier
        kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(settings.auth_issuer),
            resource_server_url=AnyHttpUrl(settings.public_mcp_url),
            required_scopes=list(settings.auth_required_scopes),
            validate_token_resource=isinstance(token_verifier, IntrospectionTokenVerifier),
        )
    mcp = MCPServer(**kwargs)

    @mcp.tool(
        title="Remember durable context",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
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
        """Durably append one memory; use only for context worth retaining beyond this chat/task.

        This adds data but never deletes or overwrites an existing memory. Replays are only
        deduplicated when the caller supplies a stable idempotency_key. Vector indexing is
        asynchronous and never blocks the durable raw write.
        """
        put = MemoryPut(
            content=content,
            source_agent=source_agent,
            source_device=source_device,
            session_id=session_id,
            role=role,
            project=project,
            metadata=metadata or {},
            idempotency_key=idempotency_key,
            created_at=created_at,
        )
        return await service.put(put)

    @mcp.tool(
        title="Scan durable memories",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
    )
    async def memory_scan(limit: int = 100, cursor: str | None = None) -> dict:
        """Page raw memories for first-time/native-index import; sync clients only, not normal chat retrieval."""
        return await service.scan(limit=max(1, min(limit, 500)), cursor=cursor)

    @mcp.tool(
        title="Read memory stream",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
    )
    async def memory_since(cursor: int, limit: int = 100) -> dict:
        """Read MemoryBridge-owned writes after a cursor; intended for incremental index/sync consumers."""
        return await service.since(cursor, limit=max(1, min(limit, 500)))

    @mcp.tool(
        title="Get one memory",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
    )
    async def memory_get(memory_id: str, collection: str | None = None) -> dict | None:
        """Fetch one durable raw memory by id without changing state or forcing a retrieval strategy."""
        return await service.get(memory_id, collection)

    @mcp.tool(
        title="Read recent memories",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
    )
    async def memory_recent(limit: int = 50) -> list[dict]:
        """Read recent durable memories as a model-free last-resort path."""
        return await service.recent(limit=max(1, min(limit, 200)))

    @mcp.tool(
        title="Acknowledge memory stream",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def memory_ack(consumer: str, cursor: int, status: str = "indexed") -> dict:
        """Advance one sync consumer's delivery/index cursor; not intended for normal ChatGPT conversations."""
        return await service.ack(Ack(consumer=consumer, cursor=cursor, status=status))

    @mcp.tool(
        title="MemoryBridge status",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
    )
    async def memory_status() -> dict:
        """Read health, counts, stream head/lag, archive presence and fallback-search capability."""
        return await service.status()

    @mcp.tool(
        title="Search durable memory",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False),
    )
    async def memory_search(query: str, limit: int = 12) -> dict:
        """Find relevant prior durable context; vector search degrades to lexical and then recent raw memory."""
        result = await service.search(query, limit=max(1, min(limit, 50)))
        return result.model_dump()

    mcp._memorybridge_service = service  # type: ignore[attr-defined]
    return mcp


def main() -> None:
    settings = Settings()
    mcp = build_server(settings)
    mcp.run(
        transport="streamable-http",
        host=settings.host,
        port=settings.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(settings),
    )


if __name__ == "__main__":
    main()
