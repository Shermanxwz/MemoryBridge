from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .config import Settings
from .models import utc_now
from .qdrant import QdrantHTTP


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_path(path: Path) -> None:
    try:
        with path.open("rb") as fh:
            os.fsync(fh.fileno())
    except OSError:
        pass


class SnapshotManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.qdrant = QdrantHTTP(settings.qdrant_url, settings.qdrant_api_key, timeout=60)

    async def close(self) -> None:
        await self.qdrant.close()

    async def _export_jsonl_gz(self, collection: str, path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        h = hashlib.sha256()
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            offset: str | int | None = None
            while True:
                points, offset = await self.qdrant.scroll(collection, limit=500, offset=offset)
                for point in points:
                    line = json.dumps(point, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
                    fh.write(line)
                if offset is None or not points:
                    break
        os.replace(tmp, path)
        _fsync_path(path)
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    async def create_archive(self, collection: str) -> dict[str, Any]:
        if not await self.qdrant.collection_exists(collection):
            return {"collection": collection, "skipped": True, "reason": "collection missing"}
        snap = await self.qdrant.create_snapshot(collection)
        snapshot_name = str(snap["name"])
        folder = self.settings.archive_dir / collection
        folder.mkdir(parents=True, exist_ok=True)
        snapshot_path = folder / snapshot_name
        tmp = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
        await self.qdrant.download_snapshot_to(collection, snapshot_name, tmp)
        os.replace(tmp, snapshot_path)
        _fsync_path(snapshot_path)
        export_path = folder / (snapshot_name + ".jsonl.gz")
        export_sha = await self._export_jsonl_gz(collection, export_path)
        manifest = {
            "archive_schema": 1,
            "collection": collection,
            "created_at": utc_now(),
            "snapshot_name": snapshot_name,
            "snapshot_sha256": sha256_file(snapshot_path),
            "qdrant_checksum": snap.get("checksum"),
            "jsonl_gz": export_path.name,
            "jsonl_gz_sha256": export_sha,
        }
        manifest_path = folder / (snapshot_name + ".manifest.json")
        manifest_tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        manifest_tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        _fsync_path(manifest_tmp)
        os.replace(manifest_tmp, manifest_path)
        verified = self.verify_manifest(manifest_path)
        if not verified["ok"]:
            raise RuntimeError(f"archive verification failed immediately after write: {verified}")
        await self._prune(collection)
        return {**manifest, "verified": True}

    async def _prune(self, collection: str) -> None:
        keep = max(1, self.settings.snapshot_retention)
        folder = self.settings.archive_dir / collection
        manifests = sorted(folder.glob("*.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for manifest_path in manifests[keep:]:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                snapshot_name = manifest.get("snapshot_name")
                for candidate in (
                    folder / str(snapshot_name),
                    folder / str(manifest.get("jsonl_gz", "")),
                    manifest_path,
                ):
                    if candidate.is_file():
                        candidate.unlink()
            except Exception:
                continue
        # Keep Qdrant's internal snapshot directory bounded too.
        try:
            internal = sorted(
                await self.qdrant.list_snapshots(collection),
                key=lambda x: str(x.get("creation_time", "")),
                reverse=True,
            )
            for row in internal[keep:]:
                await self.qdrant.delete_snapshot(collection, str(row["name"]))
        except Exception:
            pass

    def latest_manifest(self, collection: str) -> tuple[Path, dict[str, Any]]:
        folder = self.settings.archive_dir / collection
        paths = sorted(folder.glob("*.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not paths:
            raise FileNotFoundError(f"no archived snapshot for {collection}")
        path = paths[0]
        return path, json.loads(path.read_text(encoding="utf-8"))

    def verify_manifest(self, manifest_path: Path) -> dict[str, Any]:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        folder = manifest_path.parent
        snap = folder / manifest["snapshot_name"]
        export = folder / manifest["jsonl_gz"]
        snap_ok = snap.exists() and sha256_file(snap) == manifest["snapshot_sha256"]
        h = hashlib.sha256()
        if export.exists():
            with export.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
        export_ok = export.exists() and h.hexdigest() == manifest["jsonl_gz_sha256"]
        return {"ok": bool(snap_ok and export_ok), "snapshot_ok": snap_ok, "export_ok": export_ok,
                "manifest": str(manifest_path)}

    async def restore_latest(self, collection: str, *, force: bool = False) -> dict[str, Any]:
        manifest_path, manifest = self.latest_manifest(collection)
        verified = self.verify_manifest(manifest_path)
        if not verified["ok"]:
            raise RuntimeError(f"archive verification failed: {verified}")
        exists = await self.qdrant.collection_exists(collection)
        if exists and not force:
            raise RuntimeError("target collection exists; refuse destructive restore without --force")
        if exists:
            await self.qdrant.delete_collection(collection)
        snapshot_path = manifest_path.parent / manifest["snapshot_name"]
        result = await self.qdrant.upload_snapshot_file(
            collection, snapshot_path, checksum=manifest.get("qdrant_checksum")
        )
        return {"restored": True, "collection": collection, "snapshot": snapshot_path.name, "result": result}
