"""Daily, local conversation summaries for MemoryBridge.

The summarizer is deliberately outside the capture and indexing reliability path:
capture keeps writing to the local spool even when the language model is down, and
this module only queues a derived summary after the source conversation is already
available locally.  The default run summarizes the previous local calendar day.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .config import Settings
from .models import MemoryPut, utc_now
from .qdrant import QdrantHTTP
from .spool import LocalSpool

DEFAULT_MODEL = "qwen3:4b-instruct"
DEFAULT_BASE_URL = "http://127.0.0.1:8199/v1"
DEFAULT_STATE_PATH = "/var/lib/memorybridge/daily-summary/state.json"
DEFAULT_SOURCE_AGENTS = ("codex", "hermes", "openclaw")
DEFAULT_CODEX_ROOTS = (
    "/root/.codex/sessions",
    "/root/.codex/archived_sessions",
)
DEFAULT_MAX_SCAN = 100_000
DEFAULT_MAX_SESSIONS = 200
DEFAULT_CHUNK_CHARS = 1_200
DEFAULT_MAX_MESSAGE_CHARS = 900
DEFAULT_MAX_TOKENS = 128
DEFAULT_CHUNK_TOKENS = 64
DEFAULT_TIMEOUT = 45.0
DEFAULT_LOOKBACK_DAYS = 7

_TEXT_ITEM_TYPES = {"input_text", "output_text", "text"}
_ROLES = {"user", "assistant"}

# These patterns are intentionally conservative.  The model should receive useful
# prose and project names, but not bearer values, private keys, or password-like
# assignments copied into a conversation.
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)(\b(?:bearer|basic)\s+)[^\s,;]+")
_ASSIGNMENT_RE = re.compile(
    r"(?ix)"
    r"(\b(?:api[_-]?key|access[_-]?key|access[_-]?token|auth[_-]?token|bearer[_-]?token|"
    r"password|passwd|passphrase|secret|token|credential|口令|密码)\b\s*(?:=|:|是|为)\s*)"
    r"(['\"]?)([^\s,'\"`;)}\]]{4,})(\2)?"
)
_OPENAI_KEY_RE = re.compile(r"\b(?:sk|sk-proj)-[A-Za-z0-9_-]{12,}")
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    timestamp: datetime
    role: str
    content: str
    source_agent: str
    session_id: str | None
    source_ref: str


@dataclass(slots=True)
class Conversation:
    source_agent: str
    session_id: str | None
    day: date
    messages: list[ConversationMessage] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.day.isoformat()}:{self.source_agent}:{self.session_id or 'day'}"


@dataclass(frozen=True, slots=True)
class SummaryConfig:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    timeout: float = DEFAULT_TIMEOUT
    max_input_chars: int = DEFAULT_CHUNK_CHARS
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS
    max_tokens: int = DEFAULT_MAX_TOKENS
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS
    max_scan: int = DEFAULT_MAX_SCAN
    max_sessions: int = DEFAULT_MAX_SESSIONS
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    state_path: Path = Path(DEFAULT_STATE_PATH)
    source_agents: tuple[str, ...] = DEFAULT_SOURCE_AGENTS
    codex_roots: tuple[Path, ...] = tuple(Path(value) for value in DEFAULT_CODEX_ROOTS)
    timezone_name: str = "Asia/Shanghai"

    @classmethod
    def from_environment(cls, settings: Settings) -> SummaryConfig:
        def integer(name: str, default: int) -> int:
            raw = os.getenv(name, "").strip()
            if not raw:
                return default
            try:
                return max(1, int(raw))
            except ValueError:
                return default

        def floating(name: str, default: float) -> float:
            raw = os.getenv(name, "").strip()
            if not raw:
                return default
            try:
                return max(1.0, float(raw))
            except ValueError:
                return default

        agents = tuple(
            item.strip().lower()
            for item in os.getenv(
                "MEMORYBRIDGE_SUMMARY_SOURCE_AGENTS", ",".join(DEFAULT_SOURCE_AGENTS)
            ).split(",")
            if item.strip()
        )
        raw_roots = os.getenv("MEMORYBRIDGE_SUMMARY_CODEX_ROOTS", ",".join(DEFAULT_CODEX_ROOTS))
        roots = tuple(Path(os.path.expanduser(item.strip())) for item in raw_roots.split(",") if item.strip())
        timezone_name = (
            os.getenv("MEMORYBRIDGE_SUMMARY_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
        )
        state_path = Path(os.path.expanduser(os.getenv("MEMORYBRIDGE_SUMMARY_STATE", DEFAULT_STATE_PATH)))
        return cls(
            model=os.getenv("MEMORYBRIDGE_SUMMARY_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            base_url=(
                os.getenv("MEMORYBRIDGE_SUMMARY_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
            ).rstrip("/"),
            api_key=os.getenv("MEMORYBRIDGE_SUMMARY_API_KEY", "").strip() or settings.embed_api_key,
            timeout=floating("MEMORYBRIDGE_SUMMARY_TIMEOUT", DEFAULT_TIMEOUT),
            max_input_chars=integer("MEMORYBRIDGE_SUMMARY_CHUNK_CHARS", DEFAULT_CHUNK_CHARS),
            max_message_chars=integer("MEMORYBRIDGE_SUMMARY_MAX_MESSAGE_CHARS", DEFAULT_MAX_MESSAGE_CHARS),
            max_tokens=integer("MEMORYBRIDGE_SUMMARY_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            chunk_tokens=integer("MEMORYBRIDGE_SUMMARY_CHUNK_TOKENS", DEFAULT_CHUNK_TOKENS),
            max_scan=integer("MEMORYBRIDGE_SUMMARY_MAX_SCAN", DEFAULT_MAX_SCAN),
            max_sessions=integer("MEMORYBRIDGE_SUMMARY_MAX_SESSIONS", DEFAULT_MAX_SESSIONS),
            lookback_days=integer("MEMORYBRIDGE_SUMMARY_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS),
            state_path=state_path,
            source_agents=agents or DEFAULT_SOURCE_AGENTS,
            codex_roots=roots,
            timezone_name=timezone_name,
        )


def local_timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return datetime.now().astimezone().tzinfo or UTC


def parse_timestamp(value: Any, zone: tzinfo, fallback: datetime | None = None) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, UTC).astimezone(zone)
        except (OverflowError, OSError, ValueError):
            return fallback
    if not isinstance(value, str) or not value.strip():
        return fallback
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def redact_secrets(text: str) -> str:
    """Remove common credential forms before text reaches the summarizer."""
    if not text:
        return ""
    redacted = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", text)
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", redacted)

    def replace_assignment(match: re.Match[str]) -> str:
        return f"{match.group(1)}[REDACTED]"

    redacted = _ASSIGNMENT_RE.sub(replace_assignment, redacted)
    redacted = _OPENAI_KEY_RE.sub("[REDACTED_API_KEY]", redacted)
    redacted = _GITHUB_TOKEN_RE.sub("[REDACTED_GITHUB_TOKEN]", redacted)
    redacted = _SLACK_TOKEN_RE.sub("[REDACTED_SLACK_TOKEN]", redacted)
    return redacted


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if item.get("type") in _TEXT_ITEM_TYPES and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return ""


def _codex_files(roots: Sequence[Path]) -> Iterator[Path]:
    seen: set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix == ".jsonl":
            resolved = root.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield root
            continue
        if not root.is_dir():
            continue
        try:
            candidates = root.rglob("*.jsonl")
        except OSError:
            continue
        for path in candidates:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            yield path


def parse_codex_transcript(path: Path, target: date, zone: tzinfo) -> list[ConversationMessage]:
    """Extract only user/assistant text from a Codex JSONL transcript."""
    try:
        fallback = datetime.fromtimestamp(path.stat().st_mtime, zone)
    except OSError:
        fallback = datetime.now(zone)
    session_id: str | None = None
    rows: list[tuple[datetime, str, str, int]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    continue
                if row.get("type") == "session_meta":
                    value = payload.get("session_id") or payload.get("id")
                    if isinstance(value, str) and value:
                        session_id = value
                    continue
                role = payload.get("role")
                if role not in _ROLES:
                    continue
                content = _content_text(payload.get("content"))
                if not content.strip():
                    continue
                timestamp = parse_timestamp(row.get("timestamp") or payload.get("timestamp"), zone, fallback)
                if timestamp is None or timestamp.date() != target:
                    continue
                rows.append((timestamp, role, content, line_number))
    except OSError:
        return []
    resolved_session = session_id or path.stem
    return [
        ConversationMessage(
            timestamp=timestamp,
            role=role,
            content=content,
            source_agent="codex",
            session_id=resolved_session,
            source_ref=f"codex-file:{path.name}:{line_number}",
        )
        for timestamp, role, content, line_number in rows
    ]


def collect_codex_messages(roots: Sequence[Path], target: date, zone: tzinfo) -> list[ConversationMessage]:
    messages: list[ConversationMessage] = []
    for path in _codex_files(roots):
        messages.extend(parse_codex_transcript(path, target, zone))
    return messages


def _payload_content(payload: dict[str, Any]) -> str:
    for key in ("content", "text", "memory", "summary", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


async def collect_qdrant_messages(
    settings: Settings,
    target: date,
    zone: tzinfo,
    source_agents: Sequence[str],
    max_scan: int,
) -> list[ConversationMessage]:
    """Read already-captured Hermes/OpenClaw/Codex messages from raw storage."""
    client = QdrantHTTP(settings.qdrant_url, settings.qdrant_api_key, timeout=60)
    try:
        if not await client.collection_exists(settings.write_collection):
            return []
        messages: list[ConversationMessage] = []
        source_set = {item.lower() for item in source_agents}
        offset: str | int | None = None
        scanned = 0
        while scanned < max_scan:
            points, next_offset = await client.scroll(
                settings.write_collection,
                limit=min(500, max_scan - scanned),
                offset=offset,
            )
            if not points:
                break
            for point in points:
                scanned += 1
                payload = point.get("payload") or {}
                agent = str(payload.get("source_agent") or "").strip().lower()
                role = str(payload.get("role") or "").strip().lower()
                if agent not in source_set or role not in _ROLES:
                    continue
                content = _payload_content(payload)
                if not content.strip():
                    continue
                created = parse_timestamp(payload.get("created_at"), zone)
                if created is None or created.date() != target:
                    continue
                session_value = payload.get("session_id")
                session_id = str(session_value) if session_value else None
                point_id = str(point.get("id") or hashlib.sha256(content.encode()).hexdigest()[:16])
                messages.append(
                    ConversationMessage(
                        timestamp=created,
                        role=role,
                        content=content,
                        source_agent=agent,
                        session_id=session_id,
                        source_ref=f"qdrant:{point_id}",
                    )
                )
            if next_offset is None or not points:
                break
            offset = next_offset
        return messages
    finally:
        await client.close()


def _dedupe_messages(messages: Iterable[ConversationMessage]) -> list[ConversationMessage]:
    ordered = sorted(messages, key=lambda item: (item.timestamp, item.source_ref))
    file_hashes = {
        (item.role, hashlib.sha256(item.content.encode("utf-8", errors="replace")).hexdigest())
        for item in ordered
        if item.source_ref.startswith("codex-file:")
    }
    seen_refs: set[str] = set()
    result: list[ConversationMessage] = []
    for item in ordered:
        if item.source_ref in seen_refs:
            continue
        digest = hashlib.sha256(item.content.encode("utf-8", errors="replace")).hexdigest()
        if item.source_ref.startswith("qdrant:") and (item.role, digest) in file_hashes:
            continue
        seen_refs.add(item.source_ref)
        result.append(item)
    return result


def group_messages(messages: Iterable[ConversationMessage], target: date) -> list[Conversation]:
    groups: dict[tuple[str, str], Conversation] = {}
    for message in messages:
        agent = message.source_agent.lower()
        session_key = message.session_id or f"day:{target.isoformat()}"
        key = (agent, session_key)
        group = groups.setdefault(
            key,
            Conversation(source_agent=agent, session_id=message.session_id, day=target),
        )
        group.messages.append(message)
    for group in groups.values():
        group.messages = _dedupe_messages(group.messages)
    return sorted(groups.values(), key=lambda item: item.key)


def _safe_message_content(content: str, max_chars: int) -> str:
    value = redact_secrets(content).strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n[…本条消息已截断…]"


def render_message_chunks(
    messages: Sequence[ConversationMessage], *, max_chars: int, max_message_chars: int
) -> list[str]:
    blocks: list[str] = []
    for message in messages:
        stamp = message.timestamp.strftime("%H:%M:%S")
        prefix = f"[{stamp}] {message.role}: "
        content = redact_secrets(message.content).strip()
        if not content:
            continue
        segment_limit = max(1, min(max_message_chars, max_chars - len(prefix)))
        for start in range(0, len(content), segment_limit):
            block = prefix + content[start : start + segment_limit]
            blocks.append(block[:max_chars])
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for block in blocks:
        size = len(block)
        if current and current_size + size + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_size = 0
        current.append(block)
        current_size += size + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def source_digest(conversation: Conversation) -> str:
    digest = hashlib.sha256()
    for message in conversation.messages:
        digest.update(message.role.encode("utf-8"))
        digest.update(b"\0")
        digest.update(redact_secrets(message.content).strip().encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


SYSTEM_PROMPT = """你是 MemoryBridge 的本地会话整理器。你的任务是把 AI 客户端会话压缩成可检索的长期记忆。
只根据输入内容总结，不要臆测。不要复述凭据、口令、令牌、私钥或完整的工具输出；如果输入中出现敏感值，用“已脱敏”概括。
优先保留：用户目标、关键事实、已经完成的修改、重要配置/约束、未解决问题和下一步。输出简洁中文，使用清晰的小标题，不要输出思考过程。"""


def chunk_prompt(conversation: Conversation, chunk: str, index: int, total: int) -> str:
    return (
        f"这是 {conversation.day.isoformat()} 的 {conversation.source_agent} 会话片段 "
        f"（第 {index}/{total} 段）。\n"
        "请提取这一段中值得保留的事实、决策、已完成工作、未完成事项和后续动作。"
        "如果没有实质信息，写“无实质信息”。\n\n"
        f"{chunk}"
    )


def final_prompt(conversation: Conversation, material: str) -> str:
    session = conversation.session_id or "同日未标识会话"
    return (
        f"请整理 {conversation.day.isoformat()} 的 {conversation.source_agent} 会话。会话标识：{session}。\n"
        "请严格按以下结构输出：\n"
        "摘要：一句到三句概括\n"
        "已完成：\n- ...\n"
        "关键事实与决定：\n- ...\n"
        "未解决/后续：\n- ...\n"
        "没有内容的栏目写“无”。不要输出原文长引号，不要输出凭据或私密值。\n\n"
        f"会话材料：\n{material}"
    )


def merge_prompt(conversation: Conversation, material: str) -> str:
    return (
        f"这是 {conversation.day.isoformat()} 的 {conversation.source_agent} 会话的多个片段摘要。\n"
        "请合并为更短的事实清单，保留目标、决定、已完成工作和未解决事项，不要臆测，"
        "不要输出凭据或私密值。\n\n"
        f"{material}"
    )


def _completion_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else first
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


async def _complete(
    client: httpx.AsyncClient,
    config: SummaryConfig,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> str:
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"
    response = await client.post(
        f"{config.base_url}/chat/completions",
        headers=headers,
        json={
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        },
    )
    if response.status_code >= 400:
        raise RuntimeError(f"summary endpoint returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError("summary endpoint returned invalid JSON") from exc
    content = redact_secrets(_completion_text(body)).strip()
    content = re.sub(r"(?is)<think>.*?</think>", "", content).strip()
    if not content:
        raise RuntimeError("summary endpoint returned an empty completion")
    return content


async def summarize_conversation(
    conversation: Conversation, config: SummaryConfig, client: httpx.AsyncClient
) -> tuple[str, int]:
    chunks = render_message_chunks(
        conversation.messages,
        max_chars=config.max_input_chars,
        max_message_chars=config.max_message_chars,
    )
    if not chunks:
        return "无实质信息。", 0
    if len(chunks) == 1:
        return await _complete(
            client,
            config,
            SYSTEM_PROMPT,
            final_prompt(conversation, chunks[0]),
            config.max_tokens,
        ), 1
    partials: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        partials.append(
            await _complete(
                client,
                config,
                SYSTEM_PROMPT,
                chunk_prompt(conversation, chunk, index, len(chunks)),
                config.chunk_tokens,
            )
        )
    calls = len(chunks)
    compact = [redact_secrets(value).strip()[:240] for value in partials if value.strip()]
    merge_limit = max(400, config.max_input_chars - 250)
    while len(compact) > 1:
        batches: list[str] = []
        current: list[str] = []
        current_size = 0
        for index, value in enumerate(compact, start=1):
            block = f"片段摘要 {index}: {value}"
            if current and current_size + len(block) + 2 > merge_limit:
                batches.append("\n\n".join(current))
                current = []
                current_size = 0
            current.append(block)
            current_size += len(block) + 2
        if current:
            batches.append("\n\n".join(current))
        if len(batches) == 1:
            break
        compact = []
        for batch in batches:
            compact.append(
                await _complete(
                    client,
                    config,
                    SYSTEM_PROMPT,
                    merge_prompt(conversation, batch),
                    config.chunk_tokens,
                )
            )
        calls += len(batches)
    material = "\n\n".join(f"片段摘要 {index}:\n{value[:240]}" for index, value in enumerate(compact, start=1))
    final = await _complete(client, config, SYSTEM_PROMPT, final_prompt(conversation, material), config.max_tokens)
    return final, calls + 1


def _load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "completed": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("completed"), dict):
        return {"version": 1, "completed": {}}
    return raw


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass


@contextlib.contextmanager
def state_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _created_at(target: date, zone: tzinfo) -> str:
    return datetime.combine(target, time(23, 59, 59), tzinfo=zone).isoformat()


async def run_once(
    settings: Settings,
    config: SummaryConfig,
    target: date,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    zone = local_timezone(config.timezone_name)
    codex_messages = collect_codex_messages(config.codex_roots, target, zone)
    qdrant_error = ""
    try:
        qdrant_messages = await collect_qdrant_messages(
            settings, target, zone, config.source_agents, config.max_scan
        )
    except Exception as exc:
        qdrant_messages = []
        qdrant_error = type(exc).__name__

    allowed = set(config.source_agents)
    messages = [item for item in (*codex_messages, *qdrant_messages) if item.source_agent in allowed]
    groups = group_messages(messages, target)
    groups = groups[: config.max_sessions]
    result: dict[str, Any] = {
        "target_date": target.isoformat(),
        "model": config.model,
        "codex_messages": len(codex_messages),
        "qdrant_messages": len(qdrant_messages),
        "groups_found": len(groups),
        "queued": 0,
        "skipped": 0,
        "errors": 1 if qdrant_error else 0,
        "dry_run": dry_run,
    }
    if qdrant_error:
        result["qdrant_error"] = qdrant_error
    if dry_run:
        result["groups"] = [
            {
                "source_agent": group.source_agent,
                "session_id": group.session_id,
                "messages": len(group.messages),
                "characters": sum(len(item.content) for item in group.messages),
            }
            for group in groups
        ]
        return result

    with state_lock(config.state_path) as acquired:
        if not acquired:
            result["locked"] = True
            return result
        state = _load_state(config.state_path)
        completed = state.setdefault("completed", {})
        spool = LocalSpool(settings.spool_dir)
        timeout = httpx.Timeout(config.timeout, connect=min(config.timeout, 10.0), write=min(config.timeout, 10.0))
        async with httpx.AsyncClient(timeout=timeout) as client:
            for group in groups:
                digest = source_digest(group)
                previous = completed.get(group.key)
                if isinstance(previous, dict):
                    result["skipped"] += 1
                    continue
                try:
                    summary, calls = await summarize_conversation(group, config, client)
                    content = (
                        f"【每日会话摘要】{group.day.isoformat()}｜来源：{group.source_agent}"
                        f"｜消息数：{len(group.messages)}\n\n{summary}"
                    )
                    key_digest = hashlib.sha256(group.key.encode("utf-8")).hexdigest()[:32]
                    memory = MemoryPut(
                        content=content,
                        source_agent="memorybridge-daily-summary",
                        session_id=group.session_id,
                        role="memory",
                        project="MemoryBridge daily summaries",
                        metadata={
                            "summary_kind": "daily_conversation_summary",
                            "summary_date": group.day.isoformat(),
                            "summary_timezone": config.timezone_name,
                            "source_agent": group.source_agent,
                            "source_message_count": len(group.messages),
                            "source_digest": digest,
                            "summarizer_model": config.model,
                            "completion_calls": calls,
                            "capture_scope": "local_captured_conversations",
                        },
                        idempotency_key=f"daily-summary:{group.day.isoformat()}:{key_digest}",
                        created_at=_created_at(group.day, zone),
                    )
                    spool.put(memory)
                    completed[group.key] = {
                        "completed_at": utc_now(),
                        "source_digest": digest,
                        "source_message_count": len(group.messages),
                        "completion_calls": calls,
                        "model": config.model,
                    }
                    _write_state(config.state_path, state)
                    result["queued"] += 1
                except Exception as exc:
                    result["errors"] += 1
                    result.setdefault("error_types", []).append(type(exc).__name__)
                    # A provider outage affects every remaining group.  Stop after
                    # one bounded failure so the daily timer can retry promptly.
                    break
    return result


def _target_date(raw: str | None, zone: tzinfo) -> date:
    if raw:
        return date.fromisoformat(raw)
    return datetime.now(zone).date() - timedelta(days=1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize locally captured conversations into MemoryBridge")
    parser.add_argument("--date", dest="target_date", help="local date to summarize, YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="scan and report without calling the model or writing")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="include this many prior local dates; defaults to MEMORYBRIDGE_SUMMARY_LOOKBACK_DAYS",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    config = SummaryConfig.from_environment(settings)
    zone = local_timezone(config.timezone_name)
    try:
        target = _target_date(args.target_date, zone)
    except ValueError as exc:
        raise SystemExit(f"invalid --date: {exc}") from exc
    lookback_days = max(1, args.lookback_days or config.lookback_days)

    async def run_schedule() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for offset in range(lookback_days):
            results.append(
                await run_once(
                    settings,
                    config,
                    target - timedelta(days=offset),
                    dry_run=args.dry_run,
                )
            )
        return results

    results = asyncio.run(run_schedule())
    aggregate = {
        "model": config.model,
        "lookback_days": lookback_days,
        "results": results,
        "queued": sum(int(item.get("queued", 0)) for item in results),
        "skipped": sum(int(item.get("skipped", 0)) for item in results),
        "errors": sum(int(item.get("errors", 0)) for item in results),
    }
    print(json.dumps(aggregate, ensure_ascii=False, sort_keys=True))
    if aggregate["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
