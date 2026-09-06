#!/usr/bin/env python3
"""Back up MemoryBridge-owned Qdrant collections through the CloudDrive2 container.

This is intentionally additive to the operator's existing Sherman backup job.  It
only creates snapshots for the configured MemoryBridge collections and deletes a
new Qdrant snapshot after its CloudDrive2 copy, checksum, sidecar, and pointer have
all been verified.  Existing collections, snapshots, and remote files are never
needed as destructive targets for a successful run.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

QDRANT_BASE = os.getenv("MEMORYBRIDGE_BACKUP_QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
COLLECTIONS = tuple(
    item.strip()
    for item in os.getenv("MEMORYBRIDGE_BACKUP_COLLECTIONS", "memorybridge_raw,memorybridge_meta").split(",")
    if item.strip()
)
CLOUD_CONTAINER = os.getenv("MEMORYBRIDGE_BACKUP_CLOUD_CONTAINER", "clouddrive")
CLOUD_MOUNT = os.getenv("MEMORYBRIDGE_BACKUP_CLOUD_MOUNT", "/CloudDrive")
REMOTE_ROOT = os.getenv("MEMORYBRIDGE_BACKUP_REMOTE_ROOT", "/CloudDrive/qdrant-memory-backup")
REMOTE_KEEP = max(1, int(os.getenv("MEMORYBRIDGE_BACKUP_RETENTION", "5")))
STAGE_DIR = Path(os.getenv("MEMORYBRIDGE_BACKUP_STAGE_DIR", "/var/lib/memorybridge-backup/staging"))
LOCK_PATH = Path(os.getenv("MEMORYBRIDGE_BACKUP_LOCK", "/run/lock/memorybridge-backup.lock"))
LOG_PATH = os.getenv("MEMORYBRIDGE_BACKUP_LOG", "/var/log/memorybridge-backup.log")
CHUNK_SIZE = 8 * 1024 * 1024
BEIJING = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("memorybridge-cloud-backup")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    formatter.converter = time.gmtime
    try:
        handler = logging.FileHandler(LOG_PATH)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    return logger


log = setup_logging()


class BackupError(RuntimeError):
    pass


def collection_path(collection: str) -> str:
    return f"/collections/{urllib.parse.quote(collection, safe='')}"


def qdrant_json(method: str, path: str, body: object | None = None) -> object:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("MEMORYBRIDGE_BACKUP_QDRANT_API_KEY", "")
    if api_key:
        headers["api-key"] = api_key
    request = urllib.request.Request(QDRANT_BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            if not raw:
                return None
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict) and parsed.get("status") not in (None, "ok"):
                raise BackupError(f"Qdrant returned an error for {method} {path}")
            return parsed
    except BackupError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise BackupError(f"Qdrant {method} {path} failed: {exc}") from exc


def qdrant_download(collection: str, name: str, destination: Path) -> None:
    encoded_collection = urllib.parse.quote(collection, safe="")
    encoded_name = urllib.parse.quote(name, safe="")
    request = urllib.request.Request(
        f"{QDRANT_BASE}/collections/{encoded_collection}/snapshots/{encoded_name}",
        method="GET",
        headers={"api-key": os.getenv("MEMORYBRIDGE_BACKUP_QDRANT_API_KEY", "")},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise BackupError(f"snapshot download failed for {collection}/{name}: {exc}") from exc


def run_command(args: list[str], timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"command failed: {' '.join(args[:4])}: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise BackupError(f"command failed ({result.returncode}): {' '.join(args[:4])}: {detail}")
    return result


def cloud_mount_ready() -> bool:
    command = [
        "docker",
        "exec",
        CLOUD_CONTAINER,
        "sh",
        "-c",
        f"grep -q 'CloudFS {shlex.quote(CLOUD_MOUNT)} fuse' /proc/mounts",
    ]
    return run_command(command, timeout=20, check=False).returncode == 0


def wait_for_cloud_mount(timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if cloud_mount_ready():
            return
        time.sleep(5)
    raise BackupError("CloudDrive2 CloudFS mount is not ready; refusing host fallback writes")


def ensure_remote_dir(remote_dir: str) -> None:
    wait_for_cloud_mount()
    result = run_command(["docker", "exec", CLOUD_CONTAINER, "test", "-d", remote_dir], check=False)
    if result.returncode != 0:
        run_command(["docker", "exec", CLOUD_CONTAINER, "mkdir", "-p", remote_dir], timeout=120)
        run_command(["docker", "exec", CLOUD_CONTAINER, "test", "-d", remote_dir], timeout=20)


def cloud_sha256(remote_path: str) -> str:
    result = run_command(["docker", "exec", CLOUD_CONTAINER, "sha256sum", remote_path], timeout=7200)
    fields = result.stdout.strip().split()
    if not fields or len(fields[0]) != 64:
        raise BackupError(f"invalid CloudDrive2 checksum response for {remote_path}")
    return fields[0].lower()


def cloud_remove(remote_path: str) -> bool:
    result = run_command(
        ["docker", "exec", CLOUD_CONTAINER, "rm", "-f", remote_path], timeout=120, check=False
    )
    return result.returncode == 0


def cloud_move(source: str, target: str) -> None:
    run_command(["docker", "exec", CLOUD_CONTAINER, "mv", "-f", source, target], timeout=120)


def upload_file(local_path: Path, remote_path: str) -> None:
    """Stream a local file through docker exec so the write goes through CloudFS."""
    quoted = shlex.quote(remote_path)
    process = subprocess.Popen(
        ["docker", "exec", "-i", CLOUD_CONTAINER, "sh", "-c", f"cat > {quoted}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    try:
        with local_path.open("rb") as source:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                process.stdin.write(chunk)
        process.stdin.close()
        try:
            return_code = process.wait(timeout=7200)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            raise BackupError(f"CloudDrive2 upload timed out: {remote_path}") from exc
        if return_code != 0:
            error = process.stderr.read().decode("utf-8", "replace")[-500:]
            raise BackupError(f"CloudDrive2 upload failed ({return_code}): {remote_path}: {error}")
    except (BrokenPipeError, OSError) as exc:
        process.kill()
        process.wait(timeout=30)
        raise BackupError(f"CloudDrive2 upload failed: {remote_path}: {exc}") from exc


def upload_bytes(data: bytes, remote_path: str) -> None:
    with tempfile.NamedTemporaryFile(prefix="memorybridge-meta-", delete=False) as temporary:
        temporary.write(data)
        local_path = Path(temporary.name)
    try:
        upload_file(local_path, remote_path)
    finally:
        local_path.unlink(missing_ok=True)


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def parse_utc(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def remote_dir(collection: str) -> str:
    safe = Path(collection).name
    if safe != collection or not safe or safe in {".", ".."}:
        raise BackupError(f"invalid collection name: {collection!r}")
    return f"{REMOTE_ROOT.rstrip('/')}/{safe}"


def prune_remote_snapshots(collection: str) -> list[str]:
    directory = remote_dir(collection)
    listing = run_command(
        [
            "docker",
            "exec",
            CLOUD_CONTAINER,
            "sh",
            "-c",
            f"for f in {shlex.quote(directory)}/*; do "
            f"[ -e \"$f\" ] && stat -c '%Y %n' \"$f\"; done 2>/dev/null",
        ],
        timeout=60,
    )
    snapshots: list[tuple[int, str]] = []
    for line in listing.stdout.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        name = parts[1]
        if not name.endswith(".snapshot") or name.endswith(".snapshot.part"):
            continue
        try:
            snapshots.append((int(parts[0]), name))
        except ValueError:
            continue
    snapshots.sort(reverse=True)
    removed: list[str] = []
    for _mtime, path in snapshots[REMOTE_KEEP:]:
        if cloud_remove(path):
            cloud_remove(path + ".sha256")
            cloud_remove(path + ".part")
            removed.append(path)
    return removed


def backup_collection(collection: str) -> dict[str, object]:
    collection_url = collection_path(collection)
    listed = qdrant_json("GET", f"{collection_url}/snapshots")
    snapshots = listed.get("result", []) if isinstance(listed, dict) else []
    if not isinstance(snapshots, list):
        raise BackupError(f"invalid snapshot listing for {collection}")

    created = qdrant_json("POST", f"{collection_url}/snapshots?wait=true")
    result = created.get("result", {}) if isinstance(created, dict) else {}
    if not isinstance(result, dict):
        raise BackupError(f"Qdrant did not return snapshot metadata for {collection}")
    snapshot_name = result.get("name")
    if not isinstance(snapshot_name, str) or not snapshot_name:
        raise BackupError(f"Qdrant did not return a snapshot name for {collection}")
    if any(isinstance(row, dict) and row.get("name") == snapshot_name for row in snapshots):
        raise BackupError(f"new snapshot name unexpectedly collides for {collection}: {snapshot_name}")

    local_snapshot = STAGE_DIR / f"{collection}--{snapshot_name}"
    try:
        qdrant_download(collection, snapshot_name, local_snapshot)
        actual_checksum, actual_size = sha256_file(local_snapshot)
        expected_checksum = result.get("checksum")
        expected_size = result.get("size")
        if expected_checksum and actual_checksum != str(expected_checksum).lower():
            raise BackupError(
                f"Qdrant checksum mismatch for {collection}/{snapshot_name}: "
                f"expected {expected_checksum}, got {actual_checksum}"
            )
        if expected_size is not None and actual_size != int(expected_size):
            raise BackupError(
                f"Qdrant size mismatch for {collection}/{snapshot_name}: "
                f"expected {expected_size}, got {actual_size}"
            )

        directory = remote_dir(collection)
        ensure_remote_dir(directory)
        remote_path = f"{directory}/{snapshot_name}"
        cloud_remove(remote_path + ".part")
        upload_file(local_snapshot, remote_path + ".part")
        cloud_move(remote_path + ".part", remote_path)
        remote_checksum = cloud_sha256(remote_path)
        if remote_checksum != actual_checksum:
            raise BackupError(
                f"CloudDrive2 checksum mismatch for {collection}/{snapshot_name}: "
                f"local {actual_checksum}, remote {remote_checksum}"
            )

        created_at = result.get("creation_time")
        created_utc = parse_utc(created_at if isinstance(created_at, str) else None)
        manifest = {
            "schema": 1,
            "collection": collection,
            "snapshot_name": snapshot_name,
            "qdrant_creation_time_utc": created_at,
            "qdrant_creation_time_beijing": created_utc.astimezone(BEIJING).isoformat() if created_utc else None,
            "qdrant_reported_size": expected_size,
            "actual_size": actual_size,
            "qdrant_checksum": expected_checksum,
            "download_sha256": actual_checksum,
            "clouddrive2_sha256": remote_checksum,
            "remote_path": remote_path,
            "verified_at_beijing": dt.datetime.now(UTC).astimezone(BEIJING).isoformat(),
        }
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        sidecar = f"{actual_checksum}  {snapshot_name}\n".encode()
        upload_bytes(sidecar, remote_path + ".sha256.part")
        cloud_move(remote_path + ".sha256.part", remote_path + ".sha256")
        upload_bytes(manifest_bytes, f"{directory}/latest.json.part")
        cloud_move(f"{directory}/latest.json.part", f"{directory}/latest.json")

        deleted = qdrant_json("DELETE", f"{collection_url}/snapshots/{urllib.parse.quote(snapshot_name, safe='')}?wait=true")
        if not (isinstance(deleted, dict) and deleted.get("result") is True):
            raise BackupError(
                f"remote backup verified but local Qdrant snapshot deletion was not confirmed: "
                f"{collection}/{snapshot_name}"
            )
        remaining = qdrant_json("GET", f"{collection_url}/snapshots")
        remaining_names = [
            row.get("name") for row in remaining.get("result", [])
            if isinstance(row, dict) and row.get("name")
        ] if isinstance(remaining, dict) else []
        if snapshot_name in remaining_names:
            raise BackupError(f"post-cleanup snapshot verification failed: {collection}/{snapshot_name}")

        removed = prune_remote_snapshots(collection)
        log.info(
            "SUCCESS collection=%s snapshot=%s size=%d sha256=%s remote=%s remaining_local=%d pruned_remote=%d",
            collection,
            snapshot_name,
            actual_size,
            actual_checksum,
            remote_path,
            len(remaining_names),
            len(removed),
        )
        return {
            "collection": collection,
            "snapshot": snapshot_name,
            "size": actual_size,
            "sha256": actual_checksum,
            "remote_path": remote_path,
            "pruned": len(removed),
        }
    finally:
        local_snapshot.unlink(missing_ok=True)


def main() -> int:
    if not COLLECTIONS:
        log.error("no MemoryBridge backup collections configured")
        return 1
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STAGE_DIR, 0o700)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = LOCK_PATH.open("w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning("another MemoryBridge backup is already running; exiting")
        lock_file.close()
        return 0

    succeeded = 0
    failed = 0
    try:
        for collection in COLLECTIONS:
            try:
                exists = qdrant_json("GET", collection_path(collection))
                if not isinstance(exists, dict) or not exists.get("result"):
                    log.error("collection=%s is missing", collection)
                    failed += 1
                    continue
                backup_collection(collection)
                succeeded += 1
            except Exception as exc:
                log.error("FAILED collection=%s error=%s", collection, exc)
                failed += 1
        if failed or succeeded != len(COLLECTIONS):
            log.error("MemoryBridge backup incomplete: succeeded=%d failed=%d", succeeded, failed)
            return 1
        return 0
    finally:
        lock_file.close()


if __name__ == "__main__":
    sys.exit(main())
