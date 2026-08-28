from __future__ import annotations

import hashlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


Role = Literal["user", "assistant", "system", "tool", "transcript", "memory", "unknown"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


class MemoryPut(BaseModel):
    content: str = Field(min_length=1)
    source_agent: str = Field(default="unknown", min_length=1, max_length=80)
    source_device: str | None = Field(default=None, max_length=160)
    session_id: str | None = Field(default=None, max_length=256)
    role: Role = "memory"
    project: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=512)
    created_at: str | None = None

    def deterministic_id(self) -> str:
        if not self.idempotency_key:
            return str(uuid.uuid4())
        material = "\x1f".join(
            [self.source_agent, self.source_device or "", self.session_id or "", self.idempotency_key]
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "memorybridge:" + material))


class MemoryRecord(BaseModel):
    id: str
    seq: int | None = None
    content: str
    source_agent: str = "unknown"
    source_device: str | None = None
    session_id: str | None = None
    role: Role = "unknown"
    project: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    content_hash: str
    index_status: str = "not_required"
    collection: str | None = None

    @classmethod
    def from_put(cls, put: MemoryPut, *, memory_id: str, seq: int) -> MemoryRecord:
        now = utc_now()
        created = put.created_at or now
        return cls(
            id=memory_id,
            seq=seq,
            content=put.content,
            source_agent=put.source_agent,
            source_device=put.source_device,
            session_id=put.session_id,
            role=put.role,
            project=put.project,
            metadata=put.metadata,
            created_at=created,
            updated_at=now,
            content_hash=content_hash(put.content),
            index_status="pending",
        )

    def payload(self) -> dict[str, Any]:
        data = self.model_dump(exclude={"id", "collection"})
        data["memorybridge_schema"] = 1
        return data


class SearchHit(BaseModel):
    id: str
    content: str
    score: float
    collection: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    mode: Literal["vector", "lexical", "raw"]
    degraded: bool
    hits: list[SearchHit]
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class Ack(BaseModel):
    consumer: str = Field(min_length=1, max_length=160)
    cursor: int = Field(ge=0)
    status: Literal["delivered", "indexed", "acked"] = "indexed"
    updated_at: str = Field(default_factory=utc_now)


def next_time_ns(last: int) -> int:
    return max(time.time_ns(), last + 1)
