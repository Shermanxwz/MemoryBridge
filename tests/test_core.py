import asyncio
import stat
from pathlib import Path

from mcp.server import MCPServer

from memorybridge.config import Settings
from memorybridge.lexical import lexical_rank, tokens
from memorybridge.models import MemoryPut, next_time_ns
from memorybridge.server import build_server, transport_security
from memorybridge.spool import LocalSpool


def test_deterministic_idempotency_id():
    a = MemoryPut(content="x", source_agent="codex", session_id="s", idempotency_key="k")
    b = MemoryPut(content="different", source_agent="codex", session_id="s", idempotency_key="k")
    assert a.deterministic_id() == b.deterministic_id()


def test_sequence_strictly_increases():
    assert next_time_ns(10**30) == 10**30 + 1


def test_lexical_identifier_boost():
    docs = [
        ("1", "set MEMORY_OS_TOKEN in config.toml", "c", {}),
        ("2", "general memory configuration notes", "c", {}),
    ]
    hits = lexical_rank("MEMORY_OS_TOKEN", docs, limit=2)
    assert hits and hits[0].id == "1"


def test_cjk_tokens_have_bigrams():
    assert "记忆" in tokens("记忆索引")


def test_spool_is_idempotent(tmp_path: Path):
    spool = LocalSpool(tmp_path)
    item = MemoryPut(content="hello", source_agent="codex", idempotency_key="same")
    p1 = spool.put(item)
    p2 = spool.put(item)
    assert p1 == p2
    assert len(spool.pending_paths()) == 1


def test_spool_is_owner_only(tmp_path: Path):
    spool = LocalSpool(tmp_path)
    assert stat.S_IMODE(spool.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(spool.pending.stat().st_mode) == 0o700
    assert stat.S_IMODE(spool.sent.stat().st_mode) == 0o700

    pending = spool.put(MemoryPut(content="private", idempotency_key="private"))
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
    spool.mark_sent(pending)
    assert stat.S_IMODE((spool.sent / pending.name).stat().st_mode) == 0o600

    job = spool.queue_transcript(agent="codex", path=tmp_path / "transcript.jsonl", session_id="s")
    assert stat.S_IMODE(job.stat().st_mode) == 0o600


def test_transcript_job_is_small_and_replaceable(tmp_path: Path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("x" * 100000, encoding="utf-8")
    spool = LocalSpool(tmp_path / "spool")
    job1 = spool.queue_transcript(agent="codex", path=transcript, session_id="s")
    job2 = spool.queue_transcript(agent="codex", path=transcript, session_id="s")
    assert job1 == job2
    assert job1.stat().st_size < 4096
    assert len(spool.transcript_job_paths()) == 1


def test_server_constructs_with_declared_mcp_sdk():
    server = build_server(Settings())
    try:
        assert isinstance(server, MCPServer)
    finally:
        asyncio.run(server._memorybridge_service.close())


def test_server_allows_configured_reverse_proxy_host_only():
    settings = Settings(public_mcp_url="https://memory.example.com/memorybridge/mcp")
    security = transport_security(settings)
    assert "memory.example.com" in security.allowed_hosts
    assert "127.0.0.1" in security.allowed_hosts
    assert "untrusted.example.com" not in security.allowed_hosts
