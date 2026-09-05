#!/usr/bin/env python3
"""Run the MemoryBridge client/archive deployment seal from a checkout."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run() -> None:
    from memorybridge.cli import main

    sys.argv[1:1] = ["deployment-seal"]
    main()


if __name__ == "__main__":
    run()
