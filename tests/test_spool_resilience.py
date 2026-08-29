from __future__ import annotations

from pathlib import Path

import pytest

from memorybridge.config import Settings
from memorybridge.models import MemoryPut
from memorybridge.spool import LocalSpool, sync_once


@pytest.mark.asyncio
async def test_network_failure_keeps_pending_item(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    settings = Settings(spool_dir=tmp_path / "spool")
    spool = LocalSpool(settings.spool_dir)
    path = spool.put(MemoryPut(content="survive outage", idempotency_key="outage"))

    async def fail_push(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr("memorybridge.spool.push_memory", fail_push)
    result = await sync_once(settings)

    assert result["sent"] == 0
    assert result["failed"] == 1
    assert path.exists()
    assert len(spool.pending_paths()) == 1


@pytest.mark.asyncio
async def test_corrupt_item_is_quarantined_without_blocking_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    settings = Settings(spool_dir=tmp_path / "spool")
    spool = LocalSpool(settings.spool_dir)
    bad = spool.pending / "000-bad.json"
    bad.write_text("{broken", encoding="utf-8")
    spool.put(MemoryPut(content="valid", idempotency_key="valid"))
    delivered: list[str] = []

    async def record_push(_settings, memory):
        delivered.append(memory.content)

    monkeypatch.setattr("memorybridge.spool.push_memory", record_push)
    result = await sync_once(settings)

    assert result["quarantined"] == 1
    assert result["sent"] == 1
    assert delivered == ["valid"]
    assert not bad.exists()
    assert list(spool.failed.glob("memory-*.json"))


def test_corrupt_transcript_job_is_quarantined(tmp_path: Path):
    from memorybridge.spool import materialize_transcript_jobs

    settings = Settings(spool_dir=tmp_path / "spool")
    spool = LocalSpool(settings.spool_dir)
    bad = spool.transcript_jobs / "bad.json"
    bad.write_text("[]", encoding="utf-8")

    materialized, quarantined = materialize_transcript_jobs(settings)

    assert materialized == 0
    assert quarantined == 1
    assert not bad.exists()
    assert list(spool.failed.glob("transcript-*.json"))
