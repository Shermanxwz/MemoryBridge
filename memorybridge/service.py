from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any

from .config import Settings
from .embedding import EmbeddingUnavailable, OpenAICompatibleEmbedding
from .lexical import lexical_rank
from .models import Ack, MemoryPut, MemoryRecord, SearchHit, SearchResult, next_time_ns, utc_now
from .qdrant import QdrantError, QdrantHTTP

SEQ_POINT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "memorybridge:sequence-clock"))


class MemoryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.qdrant = QdrantHTTP(settings.qdrant_url, settings.qdrant_api_key)
        self.embedder = (
            OpenAICompatibleEmbedding(
                settings.embed_base_url,
                settings.embed_model,
                api_key=settings.embed_api_key,
                expected_dim=settings.embed_dim,
                timeout=settings.embed_timeout,
            )
            if settings.embedding_enabled
            else None
        )
        self._seq_lock = asyncio.Lock()
        self._last_seq: int | None = None

    async def close(self) -> None:
        await self.qdrant.close()
        if self.embedder:
            await self.embedder.close()

    async def ensure(self) -> None:
        await self.qdrant.ensure_vectorless_collection(self.settings.write_collection)
        await self.qdrant.ensure_vectorless_collection(self.settings.meta_collection)
        try:
            await self.qdrant.ensure_integer_index(self.settings.write_collection, "seq")
        except QdrantError:
            # Index creation is an optimization. memory_since has an application-side fallback.
            pass

    async def _next_seq(self) -> int:
        async with self._seq_lock:
            if self._last_seq is None:
                point = await self.qdrant.get_point(self.settings.meta_collection, SEQ_POINT_ID)
                payload = (point or {}).get("payload") or {}
                self._last_seq = int(payload.get("last_seq", 0))
            self._last_seq = next_time_ns(self._last_seq)
            await self.qdrant.upsert_payload_point(
                self.settings.meta_collection,
                SEQ_POINT_ID,
                {"kind": "sequence_clock", "last_seq": self._last_seq, "updated_at": utc_now()},
            )
            return self._last_seq

    async def put(self, put: MemoryPut) -> dict[str, Any]:
        await self.ensure()
        memory_id = put.deterministic_id()
        existing = await self.qdrant.get_point(self.settings.write_collection, memory_id)
        if existing:
            payload = existing.get("payload") or {}
            return {"id": memory_id, "seq": payload.get("seq"), "stored": True, "duplicate": True,
                    "index_status": payload.get("index_status", "unknown")}
        seq = await self._next_seq()
        record = MemoryRecord.from_put(put, memory_id=memory_id, seq=seq)
        await self.qdrant.upsert_payload_point(self.settings.write_collection, memory_id, record.payload())
        return {"id": memory_id, "seq": seq, "stored": True, "duplicate": False,
                "index_status": record.index_status}

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str:
        for key in ("content", "text", "memory", "summary", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _point_to_record(collection: str, point: dict[str, Any]) -> dict[str, Any]:
        payload = point.get("payload") or {}
        return {
            "id": str(point.get("id")),
            "collection": collection,
            "content": MemoryService._extract_content(payload),
            "payload": payload,
        }

    @staticmethod
    def _encode_scan_cursor(collection_index: int, offset: str | int | None) -> str:
        raw = json.dumps({"ci": collection_index, "offset": offset}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_scan_cursor(cursor: str | None) -> tuple[int, str | int | None]:
        if not cursor:
            return 0, None
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return int(data.get("ci", 0)), data.get("offset")

    async def scan(self, *, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        collections = self.settings.all_source_collections
        if not collections:
            return {"items": [], "next_cursor": None}
        ci, offset = self._decode_scan_cursor(cursor)
        items: list[dict[str, Any]] = []
        while ci < len(collections) and len(items) < limit:
            collection = collections[ci]
            if not await self.qdrant.collection_exists(collection):
                ci += 1
                offset = None
                continue
            points, next_offset = await self.qdrant.scroll(
                collection, limit=limit - len(items), offset=offset
            )
            items.extend(self._point_to_record(collection, p) for p in points)
            if next_offset is not None:
                return {"items": items, "next_cursor": self._encode_scan_cursor(ci, next_offset)}
            ci += 1
            offset = None
        next_cursor = self._encode_scan_cursor(ci, None) if ci < len(collections) else None
        return {"items": items, "next_cursor": next_cursor}

    async def since(self, cursor: int, *, limit: int = 100) -> dict[str, Any]:
        await self.ensure()
        filt = {"must": [{"key": "seq", "range": {"gt": int(cursor)}}]}
        try:
            points, _ = await self.qdrant.scroll(
                self.settings.write_collection,
                limit=limit + 1,
                filter_=filt,
                order_by={"key": "seq", "direction": "asc"},
            )
        except QdrantError:
            points, _ = await self.qdrant.scroll(
                self.settings.write_collection, limit=max(limit * 10, 1000), filter_=filt
            )
            points.sort(key=lambda p: int((p.get("payload") or {}).get("seq", 0)))
        has_more = len(points) > limit
        points = points[:limit]
        items = [self._point_to_record(self.settings.write_collection, p) for p in points]
        head = max([cursor, *[int(i["payload"].get("seq", 0)) for i in items]])
        return {"items": items, "cursor": head, "has_more": has_more}

    async def get(self, memory_id: str, collection: str | None = None) -> dict[str, Any] | None:
        candidates = (collection,) if collection else self.settings.all_source_collections
        for name in candidates:
            if not name or not await self.qdrant.collection_exists(name):
                continue
            point = await self.qdrant.get_point(name, memory_id)
            if point:
                return self._point_to_record(name, point)
        return None

    async def recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        await self.ensure()
        try:
            points, _ = await self.qdrant.scroll(
                self.settings.write_collection,
                limit=limit,
                order_by={"key": "seq", "direction": "desc"},
            )
        except QdrantError:
            points, _ = await self.qdrant.scroll(self.settings.write_collection, limit=max(limit * 10, 500))
            points.sort(key=lambda p: int((p.get("payload") or {}).get("seq", 0)), reverse=True)
            points = points[:limit]
        return [self._point_to_record(self.settings.write_collection, p) for p in points]

    async def ack(self, ack: Ack) -> dict[str, Any]:
        await self.ensure()
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "memorybridge:consumer:" + ack.consumer))
        await self.qdrant.upsert_payload_point(
            self.settings.meta_collection,
            point_id,
            {"kind": "consumer_ack", **ack.model_dump()},
        )
        return {"ok": True, **ack.model_dump()}

    async def _head_seq(self) -> int:
        await self.ensure()
        point = await self.qdrant.get_point(self.settings.meta_collection, SEQ_POINT_ID)
        return int(((point or {}).get("payload") or {}).get("last_seq", 0))

    async def status(self) -> dict[str, Any]:
        await self.ensure()
        collections: dict[str, Any] = {}
        discovered_fallbacks: tuple[str, ...] = ()
        try:
            names = await self.qdrant.list_collections()
            prefix = self.settings.fallback_collection + "__"
            discovered_fallbacks = tuple(name for name in names if name.startswith(prefix))
        except Exception:
            pass
        for name in dict.fromkeys(
            (*self.settings.all_source_collections, *self.settings.all_vector_collections, *discovered_fallbacks)
        ):
            try:
                collections[name] = {"exists": await self.qdrant.collection_exists(name)}
                if collections[name]["exists"]:
                    collections[name]["count"] = await self.qdrant.count(name)
            except Exception as exc:
                collections[name] = {"exists": False, "error": str(exc)}
        acks: list[dict[str, Any]] = []
        try:
            points, _ = await self.qdrant.scroll(
                self.settings.meta_collection,
                limit=1000,
                filter_={"must": [{"key": "kind", "match": {"value": "consumer_ack"}}]},
            )
            acks = [p.get("payload") or {} for p in points]
        except Exception:
            pass
        head = await self._head_seq()
        for row in acks:
            try:
                row["lag_records"] = await self.qdrant.count(
                    self.settings.write_collection,
                    filter_={"must": [{"key": "seq", "range": {"gt": int(row.get("cursor", 0))}}]},
                )
            except Exception:
                row["lag_records"] = None
        indexing: dict[str, int | None] = {"pending": None, "indexed": None}
        if await self.qdrant.collection_exists(self.settings.write_collection):
            try:
                indexing["pending"] = await self.qdrant.count(
                    self.settings.write_collection,
                    filter_={"must_not": [{"key": "index_status", "match": {"value": "indexed"}}]},
                )
                indexing["indexed"] = await self.qdrant.count(
                    self.settings.write_collection,
                    filter_={"must": [{"key": "index_status", "match": {"value": "indexed"}}]},
                )
            except Exception:
                pass
        archive: dict[str, Any] = {}
        for name in self.settings.effective_snapshot_collections:
            folder = self.settings.archive_dir / name
            manifests = sorted(folder.glob("*.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            archive[name] = {
                "latest_manifest": str(manifests[0]) if manifests else None,
                "archive_present": bool(manifests),
            }
        return {
            "service": "MemoryBridge",
            "schema": 1,
            "head_cursor": head,
            "embedding": {
                "enabled": self.settings.embedding_enabled,
                "model": self.settings.embed_model if self.settings.embedding_enabled else None,
            },
            "collections": collections,
            "consumers": acks,
            "indexing": indexing,
            "archive": archive,
            "fallback_order": ["agent-native", "qwen/qdrant-vector", "lexical", "raw/recent"],
        }

    async def _vector_search(self, query: str, limit: int) -> tuple[list[SearchHit], dict[str, Any]]:
        if not self.embedder:
            raise EmbeddingUnavailable("embedding fallback is not configured")
        vector = await self.embedder.embed(query)
        errors: dict[str, str] = {}
        generated = self.settings.fallback_generation(len(vector))
        collections = (generated, *self.settings.all_vector_collections)
        for collection in dict.fromkeys(collections):
            if not await self.qdrant.collection_exists(collection):
                continue
            try:
                points = await self.qdrant.query_vector(collection, vector, limit=limit)
                hits = []
                for p in points:
                    payload = p.get("payload") or {}
                    hits.append(
                        SearchHit(
                            id=str(p.get("id")),
                            content=self._extract_content(payload),
                            score=float(p.get("score", 0.0)),
                            collection=collection,
                            payload=payload,
                        )
                    )
                if hits:
                    return hits, {"collection": collection, "dimension": len(vector), "errors": errors}
            except Exception as exc:
                errors[collection] = str(exc)
        return [], {"dimension": len(vector), "errors": errors}

    async def _lexical_search(self, query: str, limit: int) -> list[SearchHit]:
        docs: list[tuple[str, str, str, dict[str, Any]]] = []
        remaining = self.settings.lexical_max_points
        for collection in self.settings.all_source_collections:
            if remaining <= 0 or not await self.qdrant.collection_exists(collection):
                continue
            offset: str | int | None = None
            while remaining > 0:
                batch = min(250, remaining)
                points, offset = await self.qdrant.scroll(collection, limit=batch, offset=offset)
                for p in points:
                    payload = p.get("payload") or {}
                    docs.append((str(p.get("id")), self._extract_content(payload), collection, payload))
                remaining -= len(points)
                if offset is None or not points:
                    break
        return lexical_rank(query, docs, limit=limit)

    async def search(self, query: str, *, limit: int | None = None) -> SearchResult:
        limit = max(1, min(limit or self.settings.search_top_k, 100))
        diagnostics: dict[str, Any] = {}
        try:
            vector_hits, vector_diag = await self._vector_search(query, limit)
            diagnostics["vector"] = vector_diag
            if vector_hits:
                return SearchResult(mode="vector", degraded=False, hits=vector_hits, diagnostics=diagnostics)
        except Exception as exc:
            diagnostics["vector_error"] = str(exc)
        lexical_hits = await self._lexical_search(query, limit)
        if lexical_hits:
            return SearchResult(mode="lexical", degraded=True, hits=lexical_hits, diagnostics=diagnostics)
        recent = await self.recent(limit=limit)
        raw_hits = [
            SearchHit(
                id=item["id"],
                content=item["content"],
                score=0.0,
                collection=item["collection"],
                payload=item["payload"],
            )
            for item in recent
        ]
        return SearchResult(mode="raw", degraded=True, hits=raw_hits, diagnostics=diagnostics)

    async def index_pending(self, *, limit: int = 50) -> dict[str, int]:
        if not self.embedder:
            return {"indexed": 0, "failed": 0, "skipped": 0}
        await self.ensure()
        pending: list[dict[str, Any]] = []
        offset: str | int | None = None
        # Do not only inspect the first page: otherwise old indexed points can
        # permanently starve newer pending points. Walk until we have enough work
        # or reach the end of the collection.
        while len(pending) < limit:
            points, next_offset = await self.qdrant.scroll(
                self.settings.write_collection, limit=max(100, min(limit * 5, 500)), offset=offset
            )
            pending.extend(
                p for p in points if (p.get("payload") or {}).get("index_status") != "indexed"
            )
            if next_offset is None or not points:
                break
            offset = next_offset
        pending = pending[:limit]
        indexed = failed = skipped = 0
        for p in pending:
            payload = p.get("payload") or {}
            content = self._extract_content(payload)
            try:
                vector = await self.embedder.embed(content)
                fallback_collection = self.settings.fallback_generation(len(vector))
                await self.qdrant.ensure_vector_collection(
                    fallback_collection, size=len(vector), distance="Cosine"
                )
                await self.qdrant.upsert_vector_point(
                    fallback_collection, p["id"], vector, payload
                )
                await self.qdrant.set_payload(
                    self.settings.write_collection,
                    p["id"],
                    {
                        "index_status": "indexed",
                        "indexed_at": utc_now(),
                        "embedding_model": self.settings.embed_model,
                        "fallback_collection": fallback_collection,
                    },
                )
                indexed += 1
            except Exception:
                failed += 1
        return {"indexed": indexed, "failed": failed, "skipped": skipped}
