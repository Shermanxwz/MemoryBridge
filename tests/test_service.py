import asyncio

import pytest

from memorybridge.config import Settings
from memorybridge.models import MemoryPut, SearchHit
from memorybridge.service import MemoryService


@pytest.mark.asyncio
async def test_search_degrades_vector_to_lexical(monkeypatch):
    service = MemoryService(Settings())

    async def vector_fail(query, limit):
        raise RuntimeError("embedding down")

    async def lexical_ok(query, limit):
        return [SearchHit(id="1", content="config.toml", score=2.0, collection="raw", payload={})]

    monkeypatch.setattr(service, "_vector_search", vector_fail)
    monkeypatch.setattr(service, "_lexical_search", lexical_ok)
    result = await service.search("config.toml", limit=5)
    assert result.mode == "lexical"
    assert result.degraded is True
    assert result.hits[0].id == "1"
    await service.close()


@pytest.mark.asyncio
async def test_search_degrades_to_raw_when_indexes_fail(monkeypatch):
    service = MemoryService(Settings())

    async def vector_fail(query, limit):
        raise RuntimeError("embedding down")

    async def lexical_empty(query, limit):
        return []

    async def recent(limit):
        return [{"id": "r", "content": "recent", "collection": "raw", "payload": {}}]

    monkeypatch.setattr(service, "_vector_search", vector_fail)
    monkeypatch.setattr(service, "_lexical_search", lexical_empty)
    monkeypatch.setattr(service, "recent", recent)
    result = await service.search("anything", limit=5)
    assert result.mode == "raw"
    assert result.degraded is True
    assert result.hits[0].content == "recent"
    await service.close()


class _ConcurrentPutQdrant:
    def __init__(self) -> None:
        self.points: dict[tuple[str, str], dict] = {}
        self.write_upserts = 0

    async def ensure_vectorless_collection(self, _collection):
        return None

    async def ensure_integer_index(self, _collection, _key):
        return None

    async def get_point(self, collection, point_id):
        if collection == "memorybridge_raw":
            # Yield here so the regression fails reliably if the service loses its
            # first-writer critical section.
            await asyncio.sleep(0)
        payload = self.points.get((collection, point_id))
        return {"id": point_id, "payload": dict(payload)} if payload is not None else None

    async def upsert_payload_point(self, collection, point_id, payload):
        await asyncio.sleep(0)
        self.points[(collection, point_id)] = dict(payload)
        if collection == "memorybridge_raw":
            self.write_upserts += 1

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_concurrent_idempotent_put_is_first_writer_wins():
    service = MemoryService(Settings())
    fake = _ConcurrentPutQdrant()
    await service.qdrant.close()
    service.qdrant = fake

    first = MemoryPut(
        content="first",
        source_agent="chatgpt",
        session_id="session",
        idempotency_key="same-operation",
    )
    retry = MemoryPut(
        content="different retry payload",
        source_agent="chatgpt",
        session_id="session",
        idempotency_key="same-operation",
    )

    a, b = await asyncio.gather(service.put(first), service.put(retry))

    assert a["id"] == b["id"]
    assert a["seq"] == b["seq"]
    assert {a["duplicate"], b["duplicate"]} == {False, True}
    assert fake.write_upserts == 1
    stored = fake.points[(service.settings.write_collection, a["id"])]
    assert stored["content"] == "first"
    await service.close()
