#!/usr/bin/env python3
"""Print the local MemoryBridge bearer header for Codex's HTTP MCP client."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


def main() -> int:
    path = Path(
        os.getenv("MEMORYBRIDGE_MCP_TOKEN_FILE", "~/.config/memorybridge.token")
    ).expanduser()
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise RuntimeError(f"token file must be owner-only: {path}")
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, RuntimeError) as exc:
        print(f"MemoryBridge token helper: {exc}", file=sys.stderr)
        return 1
    if not token:
        print(f"MemoryBridge token helper: empty token file: {path}", file=sys.stderr)
        return 1
    json.dump({"Authorization": f"Bearer {token}"}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
