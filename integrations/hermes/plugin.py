"""Hermes general plugin: deterministic local capture, network-free in hook callbacks."""
from __future__ import annotations

import hashlib
import socket

from memorybridge.config import Settings
from memorybridge.models import MemoryPut
from memorybridge.spool import LocalSpool


def _put(*, session_id: str | None, role: str, content: str, model: str | None = None, platform: str | None = None):
    if not content:
        return
    digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
    key = f"hermes:{session_id or ''}:{role}:{digest}"
    LocalSpool(Settings().spool_dir).put(
        MemoryPut(
            content=content,
            source_agent="hermes",
            source_device=socket.gethostname(),
            session_id=session_id,
            role=role,
            metadata={"model": model, "platform": platform},
            idempotency_key=key,
        )
    )


def post_llm_call(session_id=None, user_message="", assistant_response="", model=None, platform=None, **kwargs):
    _put(session_id=session_id, role="user", content=str(user_message or ""), model=model, platform=platform)
    _put(session_id=session_id, role="assistant", content=str(assistant_response or ""), model=model, platform=platform)


def register(ctx):
    ctx.register_hook("post_llm_call", post_llm_call)
