from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from .config import Settings
from .models import MemoryPut


def _fsync_dir(path: Path) -> None:
    """Best-effort directory fsync so an atomic rename survives a sudden power loss."""
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


class LocalSpool:
    """Crash-safe local write fallback: one atomic JSON file per unsent memory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.pending = root / "pending"
        self.sent = root / "sent"
        self.failed = root / "failed"
        self.transcript_jobs = root / "transcript-jobs"
        self.pending.mkdir(parents=True, exist_ok=True)
        self.sent.mkdir(parents=True, exist_ok=True)
        self.failed.mkdir(parents=True, exist_ok=True)
        self.transcript_jobs.mkdir(parents=True, exist_ok=True)

    def put(self, memory: MemoryPut) -> Path:
        event_id = memory.idempotency_key or uuid.uuid4().hex
        safe_id = hashlib.sha256(event_id.encode()).hexdigest()
        path = self.pending / f"{safe_id}.json"
        if path.exists() or (self.sent / path.name).exists():
            return path
        tmp = self.pending / f".{safe_id}.{os.getpid()}.tmp"
        payload = memory.model_dump(mode="json")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(self.pending)
        return path

    def pending_paths(self) -> list[Path]:
        return sorted(self.pending.glob("*.json"), key=lambda p: p.stat().st_mtime)

    def queue_transcript(
        self, *, agent: str, path: Path, session_id: str | None, source_device: str | None = None
    ) -> Path:
        """Queue a tiny transcript reference. The background daemon reads/chunks it later."""
        key = f"{agent}:{session_id or ''}:{path.resolve()}"
        safe_id = hashlib.sha256(key.encode()).hexdigest()
        target = self.transcript_jobs / f"{safe_id}.json"
        payload = {
            "agent": agent,
            "path": str(path),
            "session_id": session_id,
            "source_device": source_device,
        }
        tmp = self.transcript_jobs / f".{safe_id}.{os.getpid()}.tmp"
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        # Replace intentionally: repeated Stop/SessionEnd hooks refresh the same job.
        os.replace(tmp, target)
        _fsync_dir(self.transcript_jobs)
        return target

    def transcript_job_paths(self) -> list[Path]:
        return sorted(self.transcript_jobs.glob("*.json"), key=lambda p: p.stat().st_mtime)

    def finish_transcript_job(self, path: Path) -> None:
        try:
            path.unlink()
        finally:
            _fsync_dir(self.transcript_jobs)

    def quarantine(self, path: Path, *, prefix: str = "memory") -> Path:
        """Move malformed local data aside without deleting it or blocking later writes."""
        target = self.failed / f"{prefix}-{path.name}"
        if target.exists():
            target = self.failed / f"{prefix}-{uuid.uuid4().hex}-{path.name}"
        os.replace(path, target)
        _fsync_dir(path.parent)
        _fsync_dir(self.failed)
        return target

    def mark_sent(self, path: Path) -> None:
        target = self.sent / path.name
        os.replace(path, target)
        _fsync_dir(self.pending)
        _fsync_dir(self.sent)
        # Sent receipts are useful for short-term diagnosis but bounded automatically.
        sent = sorted(self.sent.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in sent[1000:]:
            try:
                old.unlink()
            except OSError:
                pass


def _tool_args(memory: MemoryPut) -> dict[str, Any]:
    return memory.model_dump(exclude_none=True, mode="json")


async def push_memory(settings: Settings, memory: MemoryPut) -> None:
    headers = {"Authorization": f"Bearer {settings.mcp_token}"} if settings.mcp_token else {}
    async with httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(30.0, read=120.0),
        follow_redirects=True,
    ) as http_client:
        transport = streamable_http_client(settings.mcp_url, http_client=http_client)
        async with Client(transport) as client:
            result = await client.call_tool("memory_put", _tool_args(memory))
            if getattr(result, "is_error", False):
                raise RuntimeError(str(result))


def materialize_transcript_jobs(settings: Settings, *, max_jobs: int = 8) -> tuple[int, int]:
    spool = LocalSpool(settings.spool_dir)
    materialized = quarantined = 0
    for job_path in spool.transcript_job_paths()[:max_jobs]:
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
            source_value = job.get("path") if isinstance(job, dict) else None
            if not isinstance(source_value, str) or not source_value:
                raise ValueError("transcript job has no source path")
        except (ValueError, TypeError, json.JSONDecodeError):
            spool.quarantine(job_path, prefix="transcript")
            quarantined += 1
            continue
        except OSError:
            # A transient local filesystem error is retryable; keep the job intact.
            continue

        source = Path(source_value)
        if not source.is_file():
            continue  # retain the job; the host may finish moving/flushing the transcript later
        try:
            spool_transcript(
                settings,
                agent=str(job.get("agent") or "unknown"),
                path=source,
                session_id=job.get("session_id"),
                source_device=job.get("source_device"),
            )
            spool.finish_transcript_job(job_path)
            materialized += 1
        except OSError:
            # Transcript may still be moving/flushing; retry on the next pass.
            continue
    return materialized, quarantined


async def sync_once(settings: Settings, *, max_items: int = 100) -> dict[str, int]:
    spool = LocalSpool(settings.spool_dir)
    materialized, transcript_quarantined = materialize_transcript_jobs(settings)
    sent = failed = quarantined = 0
    for path in spool.pending_paths()[:max_items]:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            failed += 1
            break
        try:
            memory = MemoryPut.model_validate_json(raw)
        except ValueError:
            # A malformed local item is not a network outage. Preserve it in dead-letter
            # storage and continue so one bad file can never brick the durable queue.
            spool.quarantine(path)
            quarantined += 1
            continue
        try:
            await push_memory(settings, memory)
            spool.mark_sent(path)
            sent += 1
        except Exception:
            failed += 1
            break  # preserve order and avoid hammering an unavailable server
    return {
        "sent": sent,
        "failed": failed,
        "quarantined": quarantined,
        "pending": len(spool.pending_paths()),
        "transcripts_materialized": materialized,
        "transcripts_quarantined": transcript_quarantined,
        "transcript_jobs": len(spool.transcript_job_paths()),
    }


async def sync_daemon(settings: Settings) -> None:
    backoff = 1.0
    while True:
        result = await sync_once(settings)
        if result["failed"]:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        else:
            backoff = 1.0
            await asyncio.sleep(2.0 if result["pending"] else 10.0)


def spool_transcript(
    settings: Settings,
    *,
    agent: str,
    path: Path,
    session_id: str | None,
    source_device: str | None = None,
    chunk_bytes: int = 32 * 1024,
) -> int:
    spool = LocalSpool(settings.spool_dir)
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        b = len(line.encode("utf-8", errors="replace"))
        if current and size + b > chunk_bytes:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(line)
        size += b
    if current:
        chunks.append("".join(current))
    for idx, chunk in enumerate(chunks):
        digest = hashlib.sha256(chunk.encode("utf-8", errors="replace")).hexdigest()
        key = f"transcript:{agent}:{session_id or ''}:{path.name}:{idx}:{digest}"
        spool.put(
            MemoryPut(
                content=chunk,
                source_agent=agent,
                source_device=source_device,
                session_id=session_id,
                role="transcript",
                metadata={"source_file": path.name, "chunk_index": idx, "chunk_count": len(chunks)},
                idempotency_key=key,
            )
        )
    return len(chunks)
