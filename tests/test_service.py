import pytest

from memorybridge.config import Settings
from memorybridge.models import SearchHit
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
