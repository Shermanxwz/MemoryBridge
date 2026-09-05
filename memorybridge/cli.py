from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .config import Settings
from .deployment import run_deployment_seal
from .models import MemoryPut
from .service import MemoryService
from .snapshot import SnapshotManager
from .spool import LocalSpool, spool_transcript, sync_daemon, sync_once


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memorybridge")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("spool-put", help="Durably queue one memory locally; never needs network")
    sp.add_argument("--agent", required=True)
    sp.add_argument("--session-id")
    sp.add_argument("--role", default="memory")
    sp.add_argument("--project")
    sp.add_argument("--idempotency-key")
    sp.add_argument("--content")
    sp.add_argument("--stdin", action="store_true")

    sub.add_parser("spool-json", help="Read a MemoryPut-compatible JSON object from stdin")

    st = sub.add_parser("spool-transcript", help="Chunk a transcript into the local crash-safe spool")
    st.add_argument("--agent", required=True)
    st.add_argument("--session-id")
    st.add_argument("--path", required=True)

    ss = sub.add_parser("spool-sync")
    ss.add_argument("--daemon", action="store_true")
    ss.add_argument("--max-items", type=int, default=100)

    sub.add_parser("status")

    sc = sub.add_parser("snapshot-create")
    sc.add_argument("--collection", action="append")

    sv = sub.add_parser("archive-verify")
    sv.add_argument("manifest")

    sr = sub.add_parser("restore-latest")
    sr.add_argument("collection")
    sr.add_argument("--force", action="store_true")

    ix = sub.add_parser("index-once")
    ix.add_argument("--limit", type=int, default=50)

    ds = sub.add_parser(
        "deployment-seal",
        help="Read-only client/archive seal; optional isolated Qdrant recovery drill",
    )
    ds.add_argument("--archive-dir", type=Path)
    ds.add_argument("--qdrant-url", help="Explicit live Qdrant URL to probe; never inferred for this command")
    ds.add_argument("--qdrant-api-key-env", default="MEMORYBRIDGE_QDRANT_API_KEY")
    ds.add_argument("--mcp-url", help="Explicit MCP URL to probe with memory_status")
    ds.add_argument("--mcp-token-env", default="MEMORYBRIDGE_MCP_TOKEN")
    ds.add_argument("--drill-qdrant-url", help="Dedicated temporary Qdrant URL for snapshot restore/delete drill")
    ds.add_argument("--drill-qdrant-api-key-env", default="MEMORYBRIDGE_QDRANT_API_KEY")
    ds.add_argument("--drill-collection", help="Optional name; must start with __memorybridge_seal_")
    ds.add_argument(
        "--archive-only",
        action="store_true",
        help="Only seal the archive node; skip live probes and recovery drill",
    )
    ds.add_argument(
        "--latest-only",
        action="store_true",
        help="Hash only the latest snapshot, not all retained snapshots",
    )
    ds.add_argument("--expected-backup-hour", type=int, default=3)
    ds.add_argument("--max-age-hours", type=float)
    return p


async def _async_main(args: argparse.Namespace, settings: Settings) -> None:
    if args.cmd == "spool-sync":
        if args.daemon:
            await sync_daemon(settings)
        else:
            _print(await sync_once(settings, max_items=args.max_items))
        return
    if args.cmd == "status":
        service = MemoryService(settings)
        try:
            _print(await service.status())
        finally:
            await service.close()
        return
    if args.cmd == "index-once":
        service = MemoryService(settings)
        try:
            _print(await service.index_pending(limit=args.limit))
        finally:
            await service.close()
        return
    manager = SnapshotManager(settings)
    try:
        if args.cmd == "snapshot-create":
            collections = args.collection or list(settings.effective_snapshot_collections)
            _print([await manager.create_archive(c) for c in collections])
        elif args.cmd == "restore-latest":
            _print(await manager.restore_latest(args.collection, force=args.force))
    finally:
        await manager.close()


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    if args.cmd == "spool-put":
        content = sys.stdin.read() if args.stdin else args.content
        if not content:
            raise SystemExit("content is required (--content or --stdin)")
        item = MemoryPut(
            content=content,
            source_agent=args.agent,
            session_id=args.session_id,
            role=args.role,
            project=args.project,
            idempotency_key=args.idempotency_key,
        )
        path = LocalSpool(settings.spool_dir).put(item)
        _print({"queued": True, "path": str(path)})
        return
    if args.cmd == "spool-json":
        item = MemoryPut.model_validate(json.load(sys.stdin))
        path = LocalSpool(settings.spool_dir).put(item)
        _print({"queued": True, "path": str(path)})
        return
    if args.cmd == "spool-transcript":
        count = spool_transcript(
            settings, agent=args.agent, path=Path(args.path), session_id=args.session_id
        )
        _print({"queued_chunks": count})
        return
    if args.cmd == "archive-verify":
        manager = SnapshotManager(settings)
        _print(manager.verify_manifest(Path(args.manifest)))
        return
    if args.cmd == "deployment-seal":
        qdrant_url = args.qdrant_url or os.getenv("MEMORYBRIDGE_QDRANT_URL")
        mcp_url = args.mcp_url or os.getenv("MEMORYBRIDGE_MCP_URL")
        result = asyncio.run(
            run_deployment_seal(
                settings,
                archive_dir=args.archive_dir,
                qdrant_url=qdrant_url,
                qdrant_api_key=os.getenv(args.qdrant_api_key_env, ""),
                mcp_url=mcp_url,
                mcp_token=os.getenv(args.mcp_token_env, ""),
                drill_qdrant_url=args.drill_qdrant_url,
                drill_qdrant_api_key=os.getenv(args.drill_qdrant_api_key_env, ""),
                drill_collection=args.drill_collection,
                archive_only=args.archive_only,
                expected_hour=args.expected_backup_hour,
                max_age_hours=args.max_age_hours,
                verify_all=not args.latest_only,
            )
        )
        _print(result)
        if not result["deployment_sealed"]:
            raise SystemExit(2)
        return
    asyncio.run(_async_main(args, settings))


if __name__ == "__main__":
    main()
