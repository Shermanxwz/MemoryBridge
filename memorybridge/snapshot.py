from __future__ import annotations

import gzip
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .models import utc_now
from .qdrant import QdrantHTTP

_MASK256 = (1 << 256) - 1


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


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _safe_archive_member(folder: Path, name: Any) -> Path:
    """Resolve a manifest filename without allowing it to escape its archive folder."""
    if not isinstance(name, str) or not name or name in {".", ".."}:
        raise ValueError("archive member name must be a non-empty filename")
    if name != Path(name).name or "/" in name or "\\" in name:
        raise ValueError("archive member must not contain a path")
    root = folder.resolve()
    candidate = (folder / name).resolve()
    if candidate.parent != root:
        raise ValueError("archive member escapes the archive directory")
    return folder / name


def _canonical_point(point: dict[str, Any]) -> bytes:
    # Portable archives intentionally certify durable memory semantics (id + payload),
    # not disposable vector/HNSW implementation details.
    portable = {"id": point.get("id"), "payload": point.get("payload") or {}}
    return json.dumps(portable, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _new_fingerprint() -> dict[str, int]:
    return {"count": 0, "sum256": 0, "xor256": 0}


def _fingerprint_add(state: dict[str, int], point: dict[str, Any]) -> None:
    value = int.from_bytes(hashlib.sha256(_canonical_point(point)).digest(), "big")
    state["count"] += 1
    state["sum256"] = (state["sum256"] + value) & _MASK256
    state["xor256"] ^= value


def _fingerprint_wire(state: dict[str, int]) -> dict[str, Any]:
    return {
        "count": state["count"],
        "sum256": f"{state['sum256']:064x}",
        "xor256": f"{state['xor256']:064x}",
    }


def _fingerprints_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        int(left.get("count", -1)) == int(right.get("count", -2))
        and str(left.get("sum256", "")) == str(right.get("sum256", "!"))
        and str(left.get("xor256", "")) == str(right.get("xor256", "!"))
    )


def fingerprint_export(path: Path) -> dict[str, Any]:
    state = _new_fingerprint()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            point = json.loads(line)
            if not isinstance(point, dict):
                raise ValueError("portable archive contains a non-object record")
            _fingerprint_add(state, point)
    return _fingerprint_wire(state)


class SnapshotManager:
    """Create self-verifying Qdrant archives and prove that every new snapshot restores."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.qdrant = QdrantHTTP(settings.qdrant_url, settings.qdrant_api_key, timeout=60)

    async def close(self) -> None:
        await self.qdrant.close()

    async def _collection_fingerprint(self, collection: str) -> dict[str, Any]:
        state = _new_fingerprint()
        offset: str | int | None = None
        while True:
            points, offset = await self.qdrant.scroll(collection, limit=500, offset=offset)
            for point in points:
                _fingerprint_add(state, point)
            if offset is None or not points:
                break
        return _fingerprint_wire(state)

    async def _export_jsonl_gz(self, collection: str, path: Path) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        state = _new_fingerprint()
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            offset: str | int | None = None
            while True:
                points, offset = await self.qdrant.scroll(collection, limit=500, offset=offset)
                for point in points:
                    portable = {"id": point.get("id"), "payload": point.get("payload") or {}}
                    line = json.dumps(portable, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
                    fh.write(line)
                    _fingerprint_add(state, portable)
                if offset is None or not points:
                    break
        os.replace(tmp, path)
        _fsync_path(path)
        _fsync_dir(path.parent)
        return {"sha256": sha256_file(path), "fingerprint": _fingerprint_wire(state)}

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
        _fsync_dir(folder)

        # A snapshot is not considered an archive until it has been restored into a
        # disposable collection. The portable JSONL is exported from that restored
        # collection, so snapshot and portable archive are guaranteed to describe the
        # same point-in-time even if the live collection is still receiving writes.
        verify_collection = f"__memorybridge_verify_{uuid.uuid4().hex}"
        export_path = folder / (snapshot_name + ".jsonl.gz")
        restore_result: Any = None
        try:
            restore_result = await self.qdrant.upload_snapshot_file(
                verify_collection,
                snapshot_path,
                checksum=snap.get("checksum"),
            )
            restored_fingerprint = await self._collection_fingerprint(verify_collection)
            export_meta = await self._export_jsonl_gz(verify_collection, export_path)
            if not _fingerprints_equal(restored_fingerprint, export_meta["fingerprint"]):
                raise RuntimeError("snapshot restore/export semantic fingerprint mismatch")
        finally:
            try:
                if await self.qdrant.collection_exists(verify_collection):
                    await self.qdrant.delete_collection(verify_collection)
            except Exception:
                # A leaked verification collection is harmless; it is uniquely named and
                # never used by the service. Do not invalidate a proven archive for cleanup.
                pass

        manifest = {
            "archive_schema": 2,
            "collection": collection,
            "created_at": utc_now(),
            "snapshot_name": snapshot_name,
            "snapshot_sha256": sha256_file(snapshot_path),
            "qdrant_checksum": snap.get("checksum"),
            "jsonl_gz": export_path.name,
            "jsonl_gz_sha256": export_meta["sha256"],
            "records_fingerprint": export_meta["fingerprint"],
            "restore_drill": {"ok": True, "result": restore_result},
        }
        manifest_path = folder / (snapshot_name + ".manifest.json")
        manifest_tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        manifest_tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        _fsync_path(manifest_tmp)
        os.replace(manifest_tmp, manifest_path)
        _fsync_dir(folder)

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
                    _safe_archive_member(folder, snapshot_name),
                    _safe_archive_member(folder, manifest.get("jsonl_gz")),
                    manifest_path,
                ):
                    if candidate.is_file():
                        candidate.unlink()
                _fsync_dir(folder)
            except Exception:
                continue
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
        snap = _safe_archive_member(folder, manifest["snapshot_name"])
        export = _safe_archive_member(folder, manifest["jsonl_gz"])
        snap_ok = snap.exists() and sha256_file(snap) == manifest["snapshot_sha256"]
        export_ok = export.exists() and sha256_file(export) == manifest["jsonl_gz_sha256"]
        semantic_ok: bool | None = None
        if export_ok and manifest.get("records_fingerprint"):
            semantic_ok = _fingerprints_equal(fingerprint_export(export), manifest["records_fingerprint"])
        ok = bool(snap_ok and export_ok and semantic_ok is not False)
        return {
            "ok": ok,
            "snapshot_ok": snap_ok,
            "export_ok": export_ok,
            "semantic_ok": semantic_ok,
            "archive_schema": manifest.get("archive_schema", 1),
            "manifest": str(manifest_path),
        }

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
        snapshot_path = _safe_archive_member(manifest_path.parent, manifest["snapshot_name"])
        result = await self.qdrant.upload_snapshot_file(
            collection, snapshot_path, checksum=manifest.get("qdrant_checksum")
        )
        semantic_verified: bool | None = None
        expected = manifest.get("records_fingerprint")
        if expected:
            actual = await self._collection_fingerprint(collection)
            semantic_verified = _fingerprints_equal(actual, expected)
            if not semantic_verified:
                raise RuntimeError(
                    "restored collection does not match archived semantic fingerprint; "
                    "archive or restore is not trustworthy"
                )
        return {
            "restored": True,
            "collection": collection,
            "snapshot": snapshot_path.name,
            "semantic_verified": semantic_verified,
            "result": result,
        }
