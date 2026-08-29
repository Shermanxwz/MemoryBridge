"""Hermes native plugin: export persisted session messages into the crash-safe local spool."""
from __future__ import annotations

import hashlib
import json
import socket
from typing import Any

from memorybridge.config import Settings
from memorybridge.models import MemoryPut
from memorybridge.spool import LocalSpool


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _spool_session(session_id: str, **context: Any) -> int:
    """Read Hermes' durable SQLite history; never perform network I/O in the hook."""
    if not session_id:
        return 0

    # Imported lazily so plugin registration remains cheap and Hermes' plugin doctor
    # can validate the module without opening the user's state database.
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        messages = db.get_messages(session_id)
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    spool = LocalSpool(Settings().spool_dir)
    device = socket.gethostname()
    count = 0
    for index, message in enumerate(messages or []):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        content = _content_text(message.get("content"))
        if not content:
            # Tool-only rows can still carry durable information.
            content = _content_text(message.get("tool_calls") or message.get("tool_name"))
        if not content:
            continue
        message_id = message.get("id")
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        stable_part = str(message_id) if message_id is not None else f"{index}:{digest}"
        key = f"hermes:{session_id}:{stable_part}:{role}"
        metadata = {
            "hermes_message_id": message_id,
            "message_index": index,
            "model": context.get("model"),
            "platform": context.get("platform") or context.get("source"),
        }
        spool.put(
            MemoryPut(
                content=content,
                source_agent="hermes",
                source_device=device,
                session_id=session_id,
                role=role if role in {"user", "assistant", "system", "tool"} else "unknown",
                metadata={k: v for k, v in metadata.items() if v is not None},
                idempotency_key=key,
            )
        )
        count += 1
    return count


def on_session_finalize(session_id: str = "", **kwargs: Any) -> None:
    # Hermes itself already persisted the full conversation to state.db. If this
    # local export ever fails, Hermes' source transcript remains intact and can be
    # replayed; no remote dependency is allowed in this callback.
    try:
        _spool_session(str(session_id or ""), **kwargs)
    except Exception:
        return


def register(ctx: Any) -> None:
    ctx.register_hook("on_session_finalize", on_session_finalize)
