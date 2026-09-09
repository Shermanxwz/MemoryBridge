from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import AnyHttpUrl

from .auth import IntrospectionTokenVerifier, build_token_verifier
from .chatgpt_ui import (
    ARCHIVE_LEGACY_RESOURCE_URI,
    ARCHIVE_RESOURCE_URI,
    ARCHIVE_UI_META,
    ARCHIVE_WIDGET_HTML,
    ARCHIVE_WIDGET_META,
)
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


_ARCHIVE_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)(?:\b(?:password|passwd|pwd|api[-_ ]?key|bearer(?:[-_ ]?token)?|access[-_ ]?token|"
        r"refresh[-_ ]?token|client[-_ ]?secret|private[-_ ]?key|secret)\b|密码|口令|令牌|密钥|私钥|验证码|授权码)"
        r"\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"\b(?:sk|rk|ghp|github_pat)_[A-Za-z0-9_]{16,}\b"),
)


def _archive_contains_credential(content: str) -> bool:
    return any(pattern.search(content) for pattern in _ARCHIVE_SECRET_PATTERNS)


def _ui_result(payload: dict) -> CallToolResult:
    """Return model-readable text plus structured data for an MCP Apps widget."""
    return CallToolResult(
        content=[TextContent(text=json.dumps(payload, ensure_ascii=False))],
        structuredContent=payload,
    )


def build_server(settings: Settings | None = None) -> MCPServer:
    settings = settings or Settings()
    service = MemoryService(settings)
    token_verifier = build_token_verifier(settings)

    base_instructions = (
        "Portable durable memory source for ChatGPT, Codex and other MCP clients. "
        "Prefer a host agent's native memory index when it exists. Use memory_search for prior durable context. "
        "Use memory_put only for information worth retaining beyond the current conversation or task. "
        "Never treat MCP connectivity alone as permission to capture every conversation."
    )
    if settings.chatgpt_ui_enabled:
        base_instructions += (
            " In ChatGPT, when the user explicitly invokes @MemoryBridge by itself or asks to open the "
            "MemoryBridge archive card, first prepare a concise durable summary of the current conversation "
            "(including only useful outcomes, decisions and next steps; never credentials), then call "
            "memorybridge_archive_panel exactly once with that draft. The panel is UI-only and never writes. "
            "The card button directly calls memorybridge_archive_save through the MCP Apps bridge; it does not "
            "send a follow-up chat message. Report success only when stored=true."
        )

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
        "instructions": base_instructions,
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

    if settings.chatgpt_ui_enabled:

        @mcp.resource(
            ARCHIVE_RESOURCE_URI,
            name="MemoryBridge archive card",
            title="MemoryBridge 归档",
            description="Interactive ChatGPT card for user-initiated durable conversation summaries.",
            mime_type="text/html;profile=mcp-app",
            meta=ARCHIVE_WIDGET_META,
        )
        def memorybridge_archive_widget() -> str:
            return ARCHIVE_WIDGET_HTML

        @mcp.resource(
            ARCHIVE_LEGACY_RESOURCE_URI,
            name="MemoryBridge archive card (legacy URI)",
            title="MemoryBridge 归档",
            description="Compatibility URI for the user-initiated durable conversation summary card.",
            mime_type="text/html;profile=mcp-app",
            meta=ARCHIVE_WIDGET_META,
        )
        def memorybridge_archive_widget_legacy() -> str:
            return ARCHIVE_WIDGET_HTML

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

    if settings.chatgpt_ui_enabled:

        @mcp.tool(
            name="memorybridge_archive_panel",
            title="Open MemoryBridge archive card",
            description=(
                "Open the prominent MemoryBridge archive card in ChatGPT. Call this when the user explicitly "
                "invokes @MemoryBridge by itself or asks to archive the current conversation. First prepare a "
                "concise safe summary and pass it in summary, with optional title, decisions, next_steps, "
                "project and session_id. This tool only renders a review card; it never writes memory. The "
                "card button directly calls memorybridge_archive_save through the MCP Apps bridge."
            ),
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
            meta=ARCHIVE_UI_META,
        )
        async def memorybridge_archive_panel(
            summary: str = "",
            title: str | None = None,
            decisions: list[str] | None = None,
            next_steps: list[str] | None = None,
            project: str | None = None,
            session_id: str | None = None,
            idempotency_key: str | None = None,
        ) -> CallToolResult:
            """Render a review card for a prepared summary without writing memory."""
            summary = (summary or "").strip()
            if not summary:
                return _ui_result(
                    {
                        "state": "draft_required",
                        "stored": False,
                        "title": "归档当前对话",
                        "description": "请先生成摘要草稿，再确认保存。",
                        "button_label": "等待摘要草稿",
                        "write_collection": settings.write_collection,
                    }
                )
            if len(summary) > 12000:
                return _ui_result({"state": "invalid", "stored": False, "error": "摘要过长，请压缩为持久上下文摘要。"})

            clean_title = (title or "").strip()
            decision_items = [item.strip() for item in (decisions or []) if item and item.strip()]
            next_step_items = [item.strip() for item in (next_steps or []) if item and item.strip()]
            sections = []
            if clean_title:
                sections.append(f"标题：{clean_title}")
            sections.append(f"摘要：{summary}")
            if decision_items:
                sections.append("关键决定：\n" + "\n".join(f"- {item}" for item in decision_items[:20]))
            if next_step_items:
                sections.append("下一步：\n" + "\n".join(f"- {item}" for item in next_step_items[:20]))
            if _archive_contains_credential("\n\n".join(sections)):
                return _ui_result(
                    {
                        "state": "invalid",
                        "stored": False,
                        "error": "摘要疑似包含凭据，已阻止展示；请移除密码、令牌或密钥后重试。",
                    }
                )
            return _ui_result(
                {
                    "state": "draft",
                    "title": clean_title or "归档当前对话",
                    "description": "摘要草稿已生成。确认后直接保存，不会发送新的对话消息。",
                    "button_label": "确认归档",
                    "summary": summary,
                    "decisions": decision_items[:20],
                    "next_steps": next_step_items[:20],
                    "project": project.strip() if project and project.strip() else None,
                    "session_id": session_id,
                    "idempotency_key": idempotency_key,
                    "write_collection": settings.write_collection,
                }
            )

        @mcp.tool(
            name="memorybridge_archive_save",
            title="Save ChatGPT archive summary",
            description=(
                "Persist one user-requested ChatGPT conversation summary in MemoryBridge. Call only after the "
                "user clicks the MemoryBridge archive card. The summary must exclude passwords, API keys, bearer "
                "tokens, private keys, cookies, one-time codes, payment data and full transcript text. Use "
                "source_agent='chatgpt'. This is a non-destructive append; pass a stable idempotency_key when "
                "retrying the same archive."
            ),
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=False,
                open_world_hint=False,
            ),
        )
        async def memorybridge_archive_save(
            summary: str,
            title: str | None = None,
            decisions: list[str] | None = None,
            next_steps: list[str] | None = None,
            project: str | None = None,
            session_id: str | None = None,
            idempotency_key: str | None = None,
        ) -> CallToolResult:
            """Save a concise user-initiated ChatGPT archive summary."""
            summary = summary.strip()
            if not summary:
                return _ui_result({"stored": False, "error": "摘要不能为空。"})
            if len(summary) > 12000:
                return _ui_result({"stored": False, "error": "摘要过长，请压缩为持久上下文摘要。"})

            decision_items = [item.strip() for item in (decisions or []) if item and item.strip()]
            next_step_items = [item.strip() for item in (next_steps or []) if item and item.strip()]
            sections = []
            if title and title.strip():
                sections.append(f"标题：{title.strip()}")
            sections.append(f"摘要：{summary}")
            if decision_items:
                sections.append("关键决定：\n" + "\n".join(f"- {item}" for item in decision_items[:20]))
            if next_step_items:
                sections.append("下一步：\n" + "\n".join(f"- {item}" for item in next_step_items[:20]))
            content = "\n\n".join(sections)
            if _archive_contains_credential(content):
                return _ui_result(
                    {
                        "stored": False,
                        "error": "摘要疑似包含凭据，已阻止写入；请移除密码、令牌或密钥后重试。",
                    }
                )

            metadata = {
                "archive_trigger": "chatgpt_archive_card",
                "capture_mode": "user_initiated_summary",
                "title": (title or "").strip() or None,
                "decisions": decision_items[:20],
                "next_steps": next_step_items[:20],
            }
            put = MemoryPut(
                content=content,
                source_agent="chatgpt",
                source_device="chatgpt-app",
                session_id=session_id,
                role="memory",
                project=project.strip() if project and project.strip() else None,
                metadata=metadata,
                idempotency_key=idempotency_key,
            )
            result = await service.put(put)
            return _ui_result(
                {
                    **result,
                    "title": title,
                    "archive_trigger": "chatgpt_archive_card",
                    "backup": "included_in_next_scheduled_qdrant_snapshot",
                }
            )

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
