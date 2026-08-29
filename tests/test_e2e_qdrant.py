from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from memorybridge.config import Settings
from memorybridge.models import MemoryPut
from memorybridge.qdrant import QdrantHTTP
from memorybridge.service import MemoryService
from memorybridge.snapshot import SnapshotManager
from memorybridge.spool import LocalSpool, sync_once

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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise RuntimeError(
                f"MemoryBridge MCP server exited with code {process.returncode}; "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"MemoryBridge MCP server did not listen on port {port}")


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

        archive = await snapshots.create_archive(settings.write_collection)
        assert archive["archive_schema"] == 2
        assert archive["restore_drill"]["ok"] is True
        assert archive["records_fingerprint"]["count"] == 2
        manifest_path, _manifest = snapshots.latest_manifest(settings.write_collection)
        verified = snapshots.verify_manifest(manifest_path)
        assert verified["ok"] is True
        assert verified["semantic_ok"] is True

        await snapshots.qdrant.delete_collection(settings.write_collection)
        assert not await snapshots.qdrant.collection_exists(settings.write_collection)
        restored = await snapshots.restore_latest(settings.write_collection)
        assert restored["restored"] is True
        assert restored["semantic_verified"] is True
        assert await snapshots.qdrant.count(settings.write_collection) == 2
        recovered = await service.get(put1["id"])
        assert recovered is not None
        assert recovered["content"] == first.content

        with pytest.raises(RuntimeError, match="refuse destructive restore"):
            await snapshots.restore_latest(settings.write_collection)
        forced = await snapshots.restore_latest(settings.write_collection, force=True)
        assert forced["semantic_verified"] is True
    finally:
        await snapshots.close()
        await service.close()


@pytest.mark.asyncio
async def test_local_spool_through_bearer_protected_mcp_http_to_qdrant(tmp_path: Path):
    qdrant_url = os.environ["MEMORYBRIDGE_E2E_QDRANT_URL"].rstrip("/")
    _wait_for_qdrant(qdrant_url)
    port = _free_port()
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    token = "seal-wire-token"
    write_collection = "mb_seal_wire_raw"
    meta_collection = "mb_seal_wire_meta"
    env = os.environ.copy()
    env.update(
        {
            "MEMORYBRIDGE_QDRANT_URL": qdrant_url,
            "MEMORYBRIDGE_WRITE_COLLECTION": write_collection,
            "MEMORYBRIDGE_META_COLLECTION": meta_collection,
            "MEMORYBRIDGE_SOURCE_COLLECTIONS": "",
            "MEMORYBRIDGE_VECTOR_COLLECTIONS": "",
            "MEMORYBRIDGE_EMBED_BASE_URL": "",
            "MEMORYBRIDGE_HOST": "127.0.0.1",
            "MEMORYBRIDGE_PORT": str(port),
            "MEMORYBRIDGE_PUBLIC_MCP_URL": mcp_url,
            "MEMORYBRIDGE_AUTH_ISSUER": "https://auth.example.invalid",
            "MEMORYBRIDGE_BEARER_TOKENS": token,
        }
    )
    process = subprocess.Popen(
        ["memorybridge-server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    qdrant = QdrantHTTP(qdrant_url)
    try:
        _wait_for_port(port, process)

        unauthenticated = httpx.post(
            mcp_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
            timeout=5,
        )
        assert unauthenticated.status_code == 401

        client_settings = Settings(
            mcp_url=mcp_url,
            mcp_token=token,
            spool_dir=tmp_path / "wire-spool",
            qdrant_url=qdrant_url,
        )
        spool = LocalSpool(client_settings.spool_dir)
        spool.put(
            MemoryPut(
                content="wire path durable memory",
                source_agent="codex",
                session_id="wire-session",
                idempotency_key="wire-1",
            )
        )
        delivered = await sync_once(client_settings)
        assert delivered["sent"] == 1
        assert delivered["pending"] == 0
        assert await qdrant.count(write_collection) == 1

        headers = {"Authorization": f"Bearer {token}"}
        async with httpx2.AsyncClient(
            headers=headers, timeout=httpx2.Timeout(15.0, read=30.0)
        ) as http_client:
            transport = streamable_http_client(mcp_url, http_client=http_client)
            async with Client(transport) as client:
                since = await client.call_tool("memory_since", {"cursor": 0, "limit": 10})
                search = await client.call_tool("memory_search", {"query": "wire durable", "limit": 3})
                status = await client.call_tool("memory_status", {})
                assert not getattr(since, "is_error", False)
                assert not getattr(search, "is_error", False)
                assert not getattr(status, "is_error", False)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        await qdrant.close()
