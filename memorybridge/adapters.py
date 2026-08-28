from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import Settings
from .spool import LocalSpool


def codex_hook_main() -> None:
    """Codex SessionEnd hook: only fsync to the local spool; never wait on the network."""
    try:
        payload = json.load(sys.stdin)
        path = payload.get("transcript_path")
        if not path:
            return
        settings = Settings()
        LocalSpool(settings.spool_dir).queue_transcript(
            agent="codex",
            path=Path(path),
            session_id=payload.get("session_id"),
            source_device=os.uname().nodename if hasattr(os, "uname") else None,
        )
    except Exception:
        # Hooks must never block/kill the host agent. The transcript remains in Codex for later backfill.
        return
