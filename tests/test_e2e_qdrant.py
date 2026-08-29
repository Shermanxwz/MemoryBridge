from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from memorybridge.config import Settings
from memorybridge.models import MemoryPut
from memorybridge.service import MemoryService
from memorybridge.snapshot import SnapshotManager

pytestmark = pytest.mark.skipif(
    not os.getenv("MEMORYBRIDGE_E2E_QDRANT_URL"),
    reason="real Qdrant E2E is only run by the seal workflow",
)


class _EmbeddingHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        text = body.get("input", "")
        if isinstance(text, list):
            text = " ".join(str(x) for x in text)
        lower = str(text).lower()
        if "architecture" in lower or "omega" in lower:
            vector = [1.0, 0.05, 0.0, 0.0]
        elif "cooking" in lower or "apple" in lower:
            vector = [0.05, 1.0, 0.0, 0.0]
        else:
            vector = [0.0, 0.0, 1.0, 0.0]
        payload = json.dumps(
            {"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": vector}]}
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _wait_for_qdrant(url: str) -> None:
    deadline = time.time() + 45
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{url.rstrip('/')}/collections", timeout=2)
            if response.status_code == 200:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"Qdrant did not become ready: {last_error}")


@pytest.fixture
def embedding_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _settings(tmp_path: Path, *, embed_base_url: str) -> Settings:
    return Settings(
        qdrant_url=os.environ["MEMORYBRIDGE_E2E_QDRANT_URL"].rstrip("/"),
        qdrant_api_key="",
        write_collection="mb_seal_raw",
        source_collections=(),
        vector_collections=(),
        fallback_collection="mb_seal_fallback",
        meta_collection="mb_seal_meta",
        embed_base_url=embed_base_url,
        embed_model="seal-embedding",
        embed_dim=4 if embed_base_url else None,
        embed_timeout=1,
        lexical_max_points=5000,
        archive_dir=tmp_path / "archive",
        snapshot_collections=("mb_seal_raw",),
        snapshot_retention=2,
    )


@pytest.mark.asyncio
async def test_full_qdrant_fallback_and_recovery_chain(tmp_path: Path, embedding_server: str):
    settings = _settings(tmp_path, embed_base_url=embedding_server)
    _wait_for_qdrant(settings.qdrant_url)
    service = MemoryService(settings)
    snapshots = SnapshotManager(settings)
    try:
        await service.ensure()
        first = MemoryPut(
            content="architecture omega decision for MemoryBridge",
            source_agent="codex",
            session_id="seal-session",
            idempotency_key="seal-1",
        )
        second = MemoryPut(
            content="cooking apple notes deliberately unrelated",
            source_agent="hermes",
            session_id="seal-session",
            idempotency_key="seal-2",
        )
        put1 = await service.put(first)
        put2 = await service.put(second)
        duplicate = await service.put(first)
        assert put1["stored"] and put2["stored"]
        assert duplicate["duplicate"] is True
        assert duplicate["id"] == put1["id"]
        assert await service.qdrant.count(settings.write_collection) == 2

        since = await service.since(0, limit=10)
        assert len(since["items"]) == 2
        assert [row["payload"]["seq"] for row in since["items"]] == sorted(
            row["payload"]["seq"] for row in since["items"]
        )
        assert len((await service.scan(limit=10))["items"]) == 2
        assert len(await service.recent(limit=10)) == 2

        indexed = await service.index_pending(limit=10)
        assert indexed["indexed"] == 2
        vector = await service.search("architecture omega", limit=2)
        assert vector.mode == "vector"
        assert vector.hits[0].id == put1["id"]

        # Vector provider unavailable -> lexical fallback, with the source of truth untouched.
        degraded_settings = _settings(tmp_path, embed_base_url="http://127.0.0.1:1/v1")
        degraded = MemoryService(degraded_settings)
        try:
            lexical = await degraded.search("architecture omega", limit=2)
            assert lexical.mode == "lexical"
            assert lexical.degraded is True
            assert lexical.hits[0].id == put1["id"]

            raw = await degraded.search("zzzz-unmatchable-needle-zzzz", limit=2)
            assert raw.mode == "raw"
            assert raw.degraded is True
            assert raw.hits
        finally:
            await degraded.close()

        # Every archive creation performs a real restore drill into a disposable
        # collection and exports JSONL from the restored snapshot, not the live source.
        archive = await snapshots.create_archive(settings.write_collection)
        assert archive["archive_schema"] == 2
        assert archive["restore_drill"]["ok"] is True
        assert archive["records_fingerprint"]["count"] == 2
        manifest_path, _manifest = snapshots.latest_manifest(settings.write_collection)
        verified = snapshots.verify_manifest(manifest_path)
        assert verified["ok"] is True
        assert verified["semantic_ok"] is True

        # Destructive disaster drill: remove live memory and rebuild only from archive.
        await snapshots.qdrant.delete_collection(settings.write_collection)
        assert not await snapshots.qdrant.collection_exists(settings.write_collection)
        restored = await snapshots.restore_latest(settings.write_collection)
        assert restored["restored"] is True
        assert restored["semantic_verified"] is True
        assert await snapshots.qdrant.count(settings.write_collection) == 2
        recovered = await service.get(put1["id"])
        assert recovered is not None
        assert recovered["content"] == first.content

        # Safety rail: never overwrite an existing live collection implicitly.
        with pytest.raises(RuntimeError, match="refuse destructive restore"):
            await snapshots.restore_latest(settings.write_collection)
        forced = await snapshots.restore_latest(settings.write_collection, force=True)
        assert forced["semantic_verified"] is True
    finally:
        await snapshots.close()
        await service.close()
