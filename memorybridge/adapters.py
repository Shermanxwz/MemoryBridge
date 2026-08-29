from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

from .config import Settings
from .models import MemoryPut
from .spool import LocalSpool


def _codex_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    values = {
        "turn_id": payload.get("turn_id"),
        "model": payload.get("model"),
        "cwd": payload.get("cwd"),
        "permission_mode": payload.get("permission_mode"),
        "hook_event_name": payload.get("hook_event_name"),
    }
    return {key: value for key, value in values.items() if value is not None}


def codex_hook_main() -> None:
    """Codex capture hook: fsync locally only; never wait on MCP or Qdrant.

    UserPromptSubmit and Stop close the hard-crash window by durably capturing the
    current user/assistant turn before SessionEnd. SessionEnd additionally queues
    Codex's persisted transcript as an idempotent reconciliation/backfill source.
    """
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return
        settings = Settings()
        spool = LocalSpool(settings.spool_dir)
        event = str(payload.get("hook_event_name") or "")
        session_id = str(payload.get("session_id") or "") or None
        turn_id = str(payload.get("turn_id") or "") or None
        device = socket.gethostname()

        if event == "UserPromptSubmit":
            content = payload.get("prompt")
            if isinstance(content, str) and content:
                key = f"codex:{session_id or ''}:{turn_id or ''}:user"
                spool.put(
                    MemoryPut(
                        content=content,
                        source_agent="codex",
                        source_device=device,
                        session_id=session_id,
                        role="user",
                        metadata=_codex_metadata(payload),
                        idempotency_key=key,
                    )
                )
            return

        if event == "Stop":
            content = payload.get("last_assistant_message")
            if isinstance(content, str) and content:
                key = f"codex:{session_id or ''}:{turn_id or ''}:assistant"
                spool.put(
                    MemoryPut(
                        content=content,
                        source_agent="codex",
                        source_device=device,
                        session_id=session_id,
                        role="assistant",
                        metadata=_codex_metadata(payload),
                        idempotency_key=key,
                    )
                )
            return

        if event == "SessionEnd":
            path = payload.get("transcript_path")
            if not isinstance(path, str) or not path:
                return
            spool.queue_transcript(
                agent="codex",
                path=Path(path),
                session_id=session_id,
                source_device=device,
            )
    except Exception:
        # Host hooks are reliability boundaries: a capture bug must never kill Codex.
        # User/assistant turns already captured by earlier hooks remain in local spool;
        # Codex also keeps its transcript for later reconciliation.
        return
