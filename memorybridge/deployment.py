from __future__ import annotations

import json
import re
import stat
import tarfile
import uuid
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import Settings
from .qdrant import QdrantHTTP
from .snapshot import sha256_file

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _safe_error(exc: BaseException, *secrets: str) -> str:
    message = str(exc)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "<redacted>")
    return f"{type(exc).__name__}: {message[:400]}"


def _safe_filename(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{field} must be a non-empty filename")
    if value != Path(value).name or "/" in value or "\\" in value:
        raise ValueError(f"{field} must not contain a path")
    return value


def mount_info(path: Path) -> dict[str, Any]:
    """Return the longest matching Linux mount for *path*, without changing state."""
    target = str(path.expanduser().resolve())
    best: dict[str, Any] | None = None
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"status": "unavailable", "error": _safe_error(exc)}

    def decode(value: str) -> str:
        return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)

    for line in lines:
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        tail = after.split()
        if len(fields) < 6 or len(tail) < 3:
            continue
        mount_point = decode(fields[4])
        if target != mount_point and not target.startswith(mount_point.rstrip("/") + "/"):
            continue
        candidate = {
            "status": "found",
            "mount_point": mount_point,
            "filesystem": tail[0],
            "source": decode(tail[1]),
            "mount_options": fields[5],
            "super_options": tail[2],
        }
        if best is None or len(mount_point) > len(str(best.get("mount_point", ""))):
            best = candidate
    return best or {"status": "not_found"}


def _permission_report(paths: list[tuple[str, Path]]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    insecure: list[str] = []
    for label, path in paths:
        try:
            metadata = path.lstat()
        except OSError as exc:
            entries.append({"name": label, "path": str(path), "error": _safe_error(exc)})
            insecure.append(label)
            continue
        if stat.S_ISLNK(metadata.st_mode):
            entries.append({"name": label, "path": str(path), "type": "symlink"})
            insecure.append(label)
            continue
        mode = stat.S_IMODE(metadata.st_mode)
        entries.append({"name": label, "path": str(path), "mode": format(mode, "04o")})
        if mode & 0o077:
            insecure.append(label)
    return {
        "secure": not insecure,
        "insecure_paths": insecure,
        "checked": entries,
        "policy": "owner-only (0700 directories, 0600 files)",
    }


def _read_sidecar(path: Path, snapshot_name: str) -> str:
    fields = path.read_text(encoding="utf-8").strip().split()
    if len(fields) < 2 or not _SHA256.fullmatch(fields[0]):
        raise ValueError(f"invalid SHA-256 sidecar: {path.name}")
    referenced = fields[-1].lstrip("*")
    if referenced != snapshot_name:
        raise ValueError(f"sidecar {path.name} references {referenced!r}, not {snapshot_name!r}")
    return fields[0].lower()


def _snapshot_tar_metadata(path: Path) -> dict[str, Any]:
    with tarfile.open(path, mode="r:*") as archive:
        members = archive.getmembers()
        unsafe = []
        for member in members:
            name = member.name.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                unsafe.append(name)
        if unsafe:
            raise ValueError(f"snapshot contains unsafe archive member: {unsafe[0]}")

        def read_member(filename: str) -> str | None:
            candidates = [member for member in members if member.name == filename]
            if not candidates:
                candidates = [member for member in members if Path(member.name).name == filename]
            if not candidates:
                return None
            member = candidates[0]
            if not member.isreg():
                raise ValueError(f"snapshot member is not a regular file: {member.name}")
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError(f"unable to read snapshot member: {member.name}")
            return handle.read(16 * 1024).decode("utf-8", errors="strict").strip()

        version = read_member("version.info")
        config_text = read_member("config.json")
        config: dict[str, Any] = {}
        if config_text:
            parsed = json.loads(config_text)
            if not isinstance(parsed, dict):
                raise ValueError("snapshot config.json is not an object")
            config = parsed
        params = config.get("params") if isinstance(config.get("params"), dict) else {}
        vectors = params.get("vectors")
        vector_summary: dict[str, Any] = {}
        if isinstance(vectors, dict):
            for key in ("size", "distance"):
                if key in vectors:
                    vector_summary[key] = vectors[key]
        return {
            "ok": bool(members),
            "member_count": len(members),
            "has_segments": any("/segments/" in f"/{member.name}" for member in members),
            "format_version": version,
            "vectors": vector_summary,
        }


def _parse_datetime(value: Any, *, assume_timezone: tzinfo = UTC) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=assume_timezone)


def verify_external_archive(
    archive_dir: Path,
    *,
    expected_hour: int | None = 3,
    max_age_hours: float | None = None,
    verify_all: bool = True,
) -> dict[str, Any]:
    """Verify the external ``latest.json + .snapshot + .sha256`` archive format.

    The operator's server backup is a raw Qdrant snapshot archive and does not
    contain the MemoryBridge JSONL.GZ manifest. This check is read-only and
    never contacts or mutates Qdrant.
    """
    root = archive_dir.expanduser()
    integrity_errors: list[str] = []
    security_errors: list[str] = []
    schedule_errors: list[str] = []
    freshness_errors: list[str] = []
    warnings: list[str] = []
    base: dict[str, Any] = {
        "format": "external-qdrant-snapshot-v1",
        "archive_dir": str(root),
        "mount": mount_info(root),
        "write_persistence": {
            "status": "not_run",
            "reason": "seal is read-only and does not create/delete files on the CloudDrive2 mount",
        },
    }
    if not root.is_dir():
        return {
            **base,
            "ok": False,
            "integrity_ok": False,
            "security_ok": False,
            "schedule_ok": False,
            "freshness_ok": False,
            "errors": [f"archive directory does not exist: {root}"],
            "warnings": [],
        }

    latest_path = root / "latest.json"
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        if not isinstance(latest, dict):
            raise ValueError("latest.json is not an object")
        collection_value = latest.get("collection")
        if not isinstance(collection_value, str) or not collection_value.strip():
            raise ValueError("latest.json has no collection")
        collection = collection_value
        if latest.get("schema") != 1:
            raise ValueError("latest.json schema must be 1")
        snapshot_name = _safe_filename(latest.get("snapshot_name"), "snapshot_name")
        snapshot_path = root / snapshot_name
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            **base,
            "ok": False,
            "integrity_ok": False,
            "security_ok": False,
            "schedule_ok": False,
            "freshness_ok": False,
            "errors": [f"invalid latest.json: {_safe_error(exc)}"],
            "warnings": [],
        }

    snapshot_paths = sorted(root.glob("*.snapshot"))
    sidecar_paths = sorted(root.glob("*.snapshot.sha256"))
    if not snapshot_path.is_file() or snapshot_path.is_symlink():
        integrity_errors.append(f"latest snapshot is missing: {snapshot_name}")
    if not snapshot_paths:
        integrity_errors.append("no .snapshot files found")

    actual_digest: str | None = None
    actual_size: int | None = None
    sidecar_digest: str | None = None
    structural: dict[str, Any] = {"ok": False}
    if snapshot_path.is_file() and not snapshot_path.is_symlink():
        try:
            actual_size = snapshot_path.stat().st_size
            actual_digest = sha256_file(snapshot_path)
            sidecar_path = snapshot_path.with_name(snapshot_path.name + ".sha256")
            if not sidecar_path.is_file() or sidecar_path.is_symlink():
                integrity_errors.append(f"latest snapshot sidecar is missing: {sidecar_path.name}")
            else:
                sidecar_digest = _read_sidecar(sidecar_path, snapshot_name)
                if sidecar_digest != actual_digest:
                    integrity_errors.append("latest snapshot does not match its SHA-256 sidecar")
            try:
                structural = _snapshot_tar_metadata(snapshot_path)
            except (OSError, tarfile.TarError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                integrity_errors.append(f"latest snapshot structure is invalid: {_safe_error(exc)}")
            if not structural.get("ok"):
                integrity_errors.append("latest snapshot tar is empty")
        except (OSError, ValueError) as exc:
            integrity_errors.append(f"unable to read latest snapshot: {_safe_error(exc)}")

    pointer_digest_fields = ("qdrant_checksum", "download_sha256", "clouddrive2_sha256")
    pointer_digest_ok = True
    for field in pointer_digest_fields:
        value = latest.get(field)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            pointer_digest_ok = False
            integrity_errors.append(f"latest.json has no valid {field}")
        elif actual_digest is not None and value.lower() != actual_digest:
            pointer_digest_ok = False
            integrity_errors.append(f"latest.json {field} does not match the local snapshot")

    for size_field in ("qdrant_reported_size", "actual_size"):
        value = latest.get(size_field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            integrity_errors.append(f"latest.json has no valid {size_field}")
        elif actual_size is not None and value != actual_size:
            integrity_errors.append(f"latest.json {size_field} does not match the local snapshot")

    inventory: list[dict[str, Any]] = []
    digests: dict[str, str] = {}
    for snapshot in snapshot_paths:
        sidecar = snapshot.with_name(snapshot.name + ".sha256")
        item: dict[str, Any] = {"snapshot": snapshot.name, "sidecar": sidecar.name}
        try:
            if snapshot.is_symlink():
                raise ValueError("snapshot is a symlink")
            item["size"] = snapshot.stat().st_size
            if verify_all or snapshot == snapshot_path:
                digest = digests.get(snapshot.name) or sha256_file(snapshot)
                digests[snapshot.name] = digest
                item["sha256"] = digest
            if not sidecar.is_file() or sidecar.is_symlink():
                item["sidecar_ok"] = False
                integrity_errors.append(f"snapshot sidecar is missing: {sidecar.name}")
            else:
                expected = _read_sidecar(sidecar, snapshot.name)
                digest = digests.get(snapshot.name)
                item["sidecar_sha256"] = expected
                item["sidecar_ok"] = digest is None or expected == digest
                if digest is not None and expected != digest:
                    integrity_errors.append(f"snapshot does not match its sidecar: {snapshot.name}")
        except (OSError, ValueError) as exc:
            item["sidecar_ok"] = False
            integrity_errors.append(f"unable to verify {snapshot.name}: {_safe_error(exc)}")
        try:
            item["mtime_utc"] = datetime.fromtimestamp(snapshot.stat().st_mtime, UTC).isoformat()
        except OSError:
            pass
        inventory.append(item)

    snapshot_names = {path.name for path in snapshot_paths}
    sidecar_names = {path.name.removesuffix(".sha256") for path in sidecar_paths}
    for extra in sorted(sidecar_names - snapshot_names):
        integrity_errors.append(f"sidecar has no snapshot: {extra}.sha256")
    if len(snapshot_paths) != len(sidecar_paths):
        integrity_errors.append(
            f"snapshot/sidecar count mismatch: {len(snapshot_paths)} snapshots, {len(sidecar_paths)} sidecars"
        )

    created_beijing = _parse_datetime(latest.get("qdrant_creation_time_beijing"))
    created_utc = _parse_datetime(latest.get("qdrant_creation_time_utc"))
    schedule: dict[str, Any] = {
        "expected_backup_hour": expected_hour,
        "latest_creation_time_beijing": latest.get("qdrant_creation_time_beijing"),
        "latest_hour_matches": None,
        "daily_schedule_proven": False,
        "proof_boundary": (
            "latest.json proves the timestamp of a retained artifact; it does not prove that every daily run succeeded"
        ),
    }
    if expected_hour is not None:
        if created_beijing is None:
            schedule_errors.append("latest.json has no parseable Beijing creation timestamp")
        else:
            schedule["latest_hour_matches"] = created_beijing.hour == expected_hour
            if created_beijing.hour != expected_hour:
                schedule_errors.append(
                    f"latest snapshot creation hour is {created_beijing.hour}, expected {expected_hour}"
                )

    freshness: dict[str, Any] = {"max_age_hours": max_age_hours, "age_hours": None}
    age_source = created_utc or created_beijing
    if age_source is not None:
        age_hours = (datetime.now(UTC) - age_source.astimezone(UTC)).total_seconds() / 3600
        freshness["age_hours"] = round(max(0.0, age_hours), 3)
        if max_age_hours is not None and age_hours > max_age_hours:
            freshness_errors.append(f"latest snapshot is older than {max_age_hours} hours")
    elif max_age_hours is not None:
        freshness_errors.append("latest.json has no timestamp for freshness validation")

    permission_paths = [("archive_dir", root), ("latest.json", latest_path)]
    permission_paths.extend((f"snapshot:{path.name}", path) for path in snapshot_paths)
    permission_paths.extend((f"sidecar:{path.name}", path) for path in sidecar_paths)
    permissions = _permission_report(permission_paths)
    if not permissions["secure"]:
        security_errors.append("archive files or directory are readable/writable by group or other users")
        warnings.append("CloudDrive2/FUSE mode is not owner-only; tighten mount umask/permissions before sealing")

    if not verify_all:
        warnings.append("only the latest snapshot was hashed; use the default all-snapshots check for full retention verification")
    if len(snapshot_paths) > 1:
        warnings.append(
            "retained snapshot history is evidence of observed runs, not proof of an uninterrupted daily schedule"
        )

    integrity_ok = not integrity_errors
    security_ok = not security_errors
    schedule_ok = not schedule_errors
    freshness_ok = not freshness_errors
    return {
        **base,
        "ok": bool(integrity_ok and security_ok and schedule_ok and freshness_ok),
        "integrity_ok": integrity_ok,
        "security_ok": security_ok,
        "schedule_ok": schedule_ok,
        "freshness_ok": freshness_ok,
        "collection": collection,
        "latest": {
            "path": str(latest_path),
            "snapshot_name": snapshot_name,
            "actual_size": actual_size,
            "actual_sha256": actual_digest,
            "sidecar_sha256": sidecar_digest,
            "pointer_digests_match": pointer_digest_ok,
            "pointer": latest,
        },
        "snapshot_structure": structural,
        "retention": {
            "snapshot_count": len(snapshot_paths),
            "sidecar_count": len(sidecar_paths),
            "all_snapshots_hashed": verify_all,
            "items": inventory,
        },
        "permissions": permissions,
        "schedule": schedule,
        "freshness": freshness,
        "errors": [*integrity_errors, *security_errors, *schedule_errors, *freshness_errors],
        "warnings": warnings,
    }


async def probe_qdrant(url: str | None, *, api_key: str = "") -> dict[str, Any]:
    if not url:
        return {
            "status": "skipped",
            "reason": "no explicit Qdrant URL supplied; this client/archive node does not assume localhost",
        }
    client = QdrantHTTP(url, api_key, timeout=10)
    try:
        collections = await client.list_collections()
        return {"status": "pass", "reachable": True, "collection_count": len(collections)}
    except Exception as exc:
        return {"status": "fail", "reachable": False, "error": _safe_error(exc, api_key)}
    finally:
        await client.close()


async def probe_mcp(url: str | None, *, token: str = "") -> dict[str, Any]:
    if not url:
        return {
            "status": "skipped",
            "reason": "no explicit MCP URL supplied; this client/archive node does not assume localhost",
        }
    try:
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(15.0, read=30.0),
            follow_redirects=True,
        ) as http_client:
            transport = streamable_http_client(url, http_client=http_client)
            async with Client(transport) as client:
                result = await client.call_tool("memory_status", {})
                if getattr(result, "is_error", False):
                    return {"status": "fail", "tool": "memory_status", "error": "MCP tool returned an error"}
        return {"status": "pass", "reachable": True, "read_only_tool": "memory_status"}
    except Exception as exc:
        return {"status": "fail", "reachable": False, "error": _safe_error(exc, token)}


async def restore_snapshot_drill(
    archive: dict[str, Any],
    url: str | None,
    *,
    api_key: str = "",
    collection: str | None = None,
) -> dict[str, Any]:
    """Restore one external snapshot into a uniquely owned temporary collection."""
    if not url:
        return {"status": "skipped", "reason": "no explicit isolated drill Qdrant URL supplied"}
    try:
        snapshot = Path(archive["latest"]["path"]).parent / archive["latest"]["snapshot_name"]
        digest = str(archive["latest"]["actual_sha256"] or "")
        if not snapshot.is_file() or not _SHA256.fullmatch(digest):
            raise ValueError("archive must pass latest snapshot integrity before restore drill")
        target = collection or f"__memorybridge_seal_restore_{uuid.uuid4().hex}"
        _safe_filename(target, "drill collection")
        if not target.startswith("__memorybridge_seal_"):
            raise ValueError("drill collection must start with __memorybridge_seal_")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return {"status": "fail", "error": _safe_error(exc, api_key)}

    client = QdrantHTTP(url, api_key, timeout=300)
    owns_target = False
    cleanup: dict[str, Any] = {"attempted": False, "ok": None}
    result: dict[str, Any] = {"status": "fail", "collection": target}
    try:
        if await client.collection_exists(target):
            return {
                "status": "fail",
                "collection": target,
                "error": "drill collection already exists; refusing to overwrite",
            }
        owns_target = True
        upload_result = await client.upload_snapshot_file(target, snapshot, checksum=digest)
        info = await client.request("GET", f"/collections/{quote(target, safe='')}")
        count = await client.count(target)
        points, _offset = await client.scroll(target, limit=2)
        if count <= 0 or not points:
            raise RuntimeError("restored drill collection is empty")
        params = (info or {}).get("config", {}).get("params", {})
        vectors = params.get("vectors") if isinstance(params, dict) else {}
        result = {
            "status": "pass",
            "collection": target,
            "upload_accepted": bool(upload_result is not None),
            "restored_point_count": count,
            "sample_points_read": len(points),
            "vector_size": vectors.get("size") if isinstance(vectors, dict) else None,
            "distance": vectors.get("distance") if isinstance(vectors, dict) else None,
        }
    except Exception as exc:
        result = {"status": "fail", "collection": target, "error": _safe_error(exc, api_key)}
    finally:
        if owns_target:
            cleanup["attempted"] = True
            try:
                if await client.collection_exists(target):
                    await client.delete_collection(target)
                cleanup["ok"] = not await client.collection_exists(target)
            except Exception as exc:
                cleanup["ok"] = False
                cleanup["error"] = _safe_error(exc, api_key)
        await client.close()
    result["cleanup"] = cleanup
    if result["status"] == "pass" and cleanup.get("ok") is not True:
        result["status"] = "fail"
        result["error"] = "temporary drill collection cleanup was not confirmed"
    return result


async def run_deployment_seal(
    settings: Settings,
    *,
    archive_dir: Path | None = None,
    qdrant_url: str | None = None,
    qdrant_api_key: str = "",
    mcp_url: str | None = None,
    mcp_token: str = "",
    drill_qdrant_url: str | None = None,
    drill_qdrant_api_key: str = "",
    drill_collection: str | None = None,
    archive_only: bool = False,
    expected_hour: int | None = 3,
    max_age_hours: float | None = None,
    verify_all: bool = True,
) -> dict[str, Any]:
    archive = verify_external_archive(
        archive_dir or settings.archive_dir,
        expected_hour=expected_hour,
        max_age_hours=max_age_hours,
        verify_all=verify_all,
    )
    if archive_only:
        return {
            "scope": "archive-node",
            "node_role": "client/archive-verification",
            "archive": archive,
            "recovery_drill": {"status": "not_required", "reason": "archive-only mode"},
            "deployment_sealed": bool(archive["ok"]),
            "seal_state": "SEALED" if archive["ok"] else "NOT_SEALED",
        }

    qdrant = await probe_qdrant(qdrant_url, api_key=qdrant_api_key)
    mcp = await probe_mcp(mcp_url, token=mcp_token)
    drill = await restore_snapshot_drill(
        archive,
        drill_qdrant_url,
        api_key=drill_qdrant_api_key,
        collection=drill_collection,
    )
    recovery_ok = drill["status"] == "pass"
    archive_node_sealed = bool(archive["ok"] and recovery_ok)
    deployment_sealed = bool(
        archive_node_sealed and qdrant["status"] == "pass" and mcp["status"] == "pass"
    )
    return {
        "scope": "deployment",
        "node_role": "client/archive-verification",
        "archive": archive,
        "live_qdrant": qdrant,
        "mcp": mcp,
        "recovery_drill": drill,
        "archive_node_sealed": archive_node_sealed,
        "deployment_sealed": deployment_sealed,
        "seal_state": "SEALED" if deployment_sealed else "NOT_SEALED",
        "boundary": (
            "deployment SEALED requires verified archive integrity/security, an explicit live Qdrant probe, "
            "an explicit read-only MCP status probe, and a passing isolated recovery drill"
        ),
    }
