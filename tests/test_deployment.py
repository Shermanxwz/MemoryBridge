from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from memorybridge.config import Settings
from memorybridge.deployment import restore_snapshot_drill, run_deployment_seal, verify_external_archive
from memorybridge.snapshot import SnapshotManager


def _make_external_archive(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    snapshot = root / "memory-2026-09-05-19-15-03.snapshot"
    with tarfile.open(snapshot, mode="w") as archive:
        members = {
            "0/segments/segment.tar": b"segment",
            "version.info": b"0.4.2",
            "config.json": json.dumps(
                {"params": {"vectors": {"size": 768, "distance": "Cosine"}}}
            ).encode(),
        }
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(content))
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    (root / f"{snapshot.name}.sha256").write_text(f"{digest}  {snapshot.name}\n", encoding="utf-8")
    (root / "latest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "collection": "memory",
                "snapshot_name": snapshot.name,
                "qdrant_creation_time_utc": "2026-09-05T19:15:03",
                "qdrant_creation_time_beijing": "2026-09-06T03:15:03+08:00",
                "qdrant_reported_size": snapshot.stat().st_size,
                "actual_size": snapshot.stat().st_size,
                "qdrant_checksum": digest,
                "download_sha256": digest,
                "clouddrive2_sha256": digest,
                "remote_path": f"/CloudDrive/archive/{snapshot.name}",
                "verified_at_beijing": "2026-09-06T03:15:12+08:00",
            }
        ),
        encoding="utf-8",
    )
    root.chmod(0o700)
    snapshot.chmod(0o600)
    (root / f"{snapshot.name}.sha256").chmod(0o600)
    (root / "latest.json").chmod(0o600)
    return snapshot


def test_external_archive_verification_is_complete(tmp_path: Path):
    _make_external_archive(tmp_path)

    result = verify_external_archive(tmp_path)

    assert result["ok"] is True
    assert result["integrity_ok"] is True
    assert result["security_ok"] is True
    assert result["schedule_ok"] is True
    assert result["schedule"]["latest_hour_matches"] is True
    assert result["schedule"]["daily_schedule_proven"] is False
    assert result["snapshot_structure"]["vectors"] == {"size": 768, "distance": "Cosine"}
    assert result["retention"]["snapshot_count"] == 1
    assert result["retention"]["sidecar_count"] == 1
    assert result["retention"]["all_snapshots_hashed"] is True


def test_external_archive_detects_checksum_tampering(tmp_path: Path):
    snapshot = _make_external_archive(tmp_path)
    sidecar = snapshot.with_name(snapshot.name + ".sha256")
    sidecar.write_text(f"{'0' * 64}  {snapshot.name}\n", encoding="utf-8")

    result = verify_external_archive(tmp_path)

    assert result["ok"] is False
    assert result["integrity_ok"] is False
    assert any("sidecar" in error for error in result["errors"])


def test_archive_only_seal_does_not_probe_qdrant(tmp_path: Path):
    _make_external_archive(tmp_path)

    result = asyncio.run(run_deployment_seal(Settings(), archive_dir=tmp_path, archive_only=True))

    assert result["scope"] == "archive-node"
    assert result["deployment_sealed"] is True
    assert result["recovery_drill"]["status"] == "not_required"


def test_restore_drill_requires_seal_collection_prefix(tmp_path: Path):
    _make_external_archive(tmp_path)
    archive = verify_external_archive(tmp_path)

    result = asyncio.run(
        restore_snapshot_drill(
            archive,
            "http://127.0.0.1:16333",
            collection="production_collection",
        )
    )

    assert result["status"] == "fail"
    assert "__memorybridge_seal_" in result["error"]


def test_manifest_verification_rejects_path_escape(tmp_path: Path):
    manifest = tmp_path / "bad.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "snapshot_name": "../outside.snapshot",
                "jsonl_gz": "export.jsonl.gz",
                "snapshot_sha256": "0" * 64,
                "jsonl_gz_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    manager = SnapshotManager(Settings(archive_dir=tmp_path))
    try:
        with pytest.raises(ValueError, match="must not contain a path"):
            manager.verify_manifest(manifest)
    finally:
        asyncio.run(manager.close())
