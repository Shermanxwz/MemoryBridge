from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from memorybridge.config import Settings
from memorybridge.daily_summary import (
    Conversation,
    ConversationMessage,
    SummaryConfig,
    group_messages,
    parse_codex_transcript,
    redact_secrets,
    render_message_chunks,
    run_once,
    source_digest,
)


def _message(role: str, content: str, *, source: str = "test") -> ConversationMessage:
    return ConversationMessage(
        timestamp=datetime.fromisoformat("2026-09-11T10:00:00+08:00"),
        role=role,
        content=content,
        source_agent="codex",
        session_id="session-1",
        source_ref=source,
    )


def test_redaction_removes_common_credentials_without_removing_model_name():
    text = "model=qwen3:4b-instruct token=secret-value Bearer abcdefghijklmnop sk-proj-1234567890123"
    redacted = redact_secrets(text)
    assert "qwen3:4b-instruct" in redacted
    assert "secret-value" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "sk-proj-1234567890123" not in redacted


def test_parse_codex_transcript_extracts_only_user_and_assistant_text(tmp_path: Path):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type":"session_meta","payload":{"session_id":"s-1"}}',
                '{"type":"response_item","timestamp":"2026-09-11T10:00:00+08:00","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"目标"}]}}',
                '{"type":"response_item","timestamp":"2026-09-11T10:01:00+08:00","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"已完成"}]}}',
                '{"type":"response_item","timestamp":"2026-09-11T10:02:00+08:00","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"不要进入摘要"}]}}',
            ]
        ),
        encoding="utf-8",
    )
    messages = parse_codex_transcript(path, date(2026, 9, 11), ZoneInfo("Asia/Shanghai"))
    assert [(item.role, item.content) for item in messages] == [("user", "目标"), ("assistant", "已完成")]
    assert all(item.session_id == "s-1" for item in messages)


def test_grouping_merges_sources_and_deduplicates_codex_hook_copy():
    direct = _message("user", "same prompt", source="codex-file:x:1")
    captured = _message("user", "same prompt", source="qdrant:1")
    other = _message("assistant", "answer", source="qdrant:2")
    groups = group_messages([captured, other, direct], date(2026, 9, 11))
    assert len(groups) == 1
    assert [(item.role, item.content) for item in groups[0].messages] == [
        ("user", "same prompt"),
        ("assistant", "answer"),
    ]


def test_render_message_chunks_bound_each_chunk():
    messages = [_message("user", "x" * 30), _message("assistant", "y" * 30)]
    chunks = render_message_chunks(messages, max_chars=50, max_message_chars=100)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 50 for chunk in chunks)


def test_source_digest_changes_when_source_changes():
    conversation = Conversation(
        source_agent="codex",
        session_id="s",
        day=date(2026, 9, 11),
        messages=[_message("user", "one")],
    )
    first = source_digest(conversation)
    conversation.messages[0] = _message("user", "two")
    assert source_digest(conversation) != first


@pytest.mark.asyncio
async def test_run_once_queues_one_idempotent_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    codex_root = tmp_path / "codex"
    codex_root.mkdir()
    transcript = codex_root / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            [
                '{"type":"session_meta","payload":{"session_id":"s-1"}}',
                '{"type":"response_item","timestamp":"2026-09-11T10:00:00+08:00","payload":{"role":"user","content":[{"type":"input_text","text":"目标"}]}}',
                '{"type":"response_item","timestamp":"2026-09-11T10:01:00+08:00","payload":{"role":"assistant","content":[{"type":"output_text","text":"已完成"}]}}',
            ]
        ),
        encoding="utf-8",
    )

    async def no_qdrant_messages(*args, **kwargs):
        return []

    async def fake_summary(*args, **kwargs):
        return "测试摘要", 1

    monkeypatch.setattr("memorybridge.daily_summary.collect_qdrant_messages", no_qdrant_messages)
    monkeypatch.setattr("memorybridge.daily_summary.summarize_conversation", fake_summary)
    settings = Settings(spool_dir=tmp_path / "spool")
    config = SummaryConfig(
        state_path=tmp_path / "state.json",
        codex_roots=(codex_root,),
        source_agents=("codex",),
    )
    target = date(2026, 9, 11)

    first = await run_once(settings, config, target)
    second = await run_once(settings, config, target)

    assert first["queued"] == 1
    assert second["queued"] == 0
    assert second["skipped"] == 1
    assert len(list((tmp_path / "spool" / "pending").glob("*.json"))) == 1
