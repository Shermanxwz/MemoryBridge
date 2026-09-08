from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path


def _csv(name: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in os.getenv(name, "").split(",") if x.strip())


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw in (None, "") else int(raw)


def _opt_int(name: str) -> int | None:
    raw = os.getenv(name)
    return None if raw in (None, "") else int(raw)


def _client_token() -> str:
    """Read the client bearer token without requiring it in a process environment.

    Environment variables remain supported for service managers and backwards
    compatibility. The token-file form is preferred for interactive clients:
    it keeps the credential out of shell exports and lets the Codex header
    helper and spool daemon share one owner-only file.
    """
    direct = os.getenv("MEMORYBRIDGE_MCP_TOKEN", "").strip()
    if direct:
        return direct
    raw_path = os.getenv("MEMORYBRIDGE_MCP_TOKEN_FILE", "").strip()
    if not raw_path:
        return ""
    path = Path(os.path.expanduser(raw_path))
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@dataclass(frozen=True, slots=True)
class Settings:
    qdrant_url: str = os.getenv("MEMORYBRIDGE_QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
    qdrant_api_key: str = os.getenv("MEMORYBRIDGE_QDRANT_API_KEY", "")
    write_collection: str = os.getenv("MEMORYBRIDGE_WRITE_COLLECTION", "memorybridge_raw")
    source_collections: tuple[str, ...] = _csv("MEMORYBRIDGE_SOURCE_COLLECTIONS")
    vector_collections: tuple[str, ...] = _csv("MEMORYBRIDGE_VECTOR_COLLECTIONS")
    fallback_collection: str = os.getenv("MEMORYBRIDGE_FALLBACK_COLLECTION", "memorybridge_fallback")
    meta_collection: str = os.getenv("MEMORYBRIDGE_META_COLLECTION", "memorybridge_meta")

    host: str = os.getenv("MEMORYBRIDGE_HOST", "127.0.0.1")
    port: int = _int("MEMORYBRIDGE_PORT", 8765)
    public_mcp_url: str = os.getenv("MEMORYBRIDGE_PUBLIC_MCP_URL", "http://127.0.0.1:8765/mcp")
    bearer_tokens: tuple[str, ...] = _csv("MEMORYBRIDGE_BEARER_TOKENS")
    auth_issuer: str = os.getenv("MEMORYBRIDGE_AUTH_ISSUER", "https://memorybridge.invalid")
    auth_required_scopes: tuple[str, ...] = _csv("MEMORYBRIDGE_AUTH_REQUIRED_SCOPES") or ("memory",)
    oauth_introspection_url: str = os.getenv("MEMORYBRIDGE_OAUTH_INTROSPECTION_URL", "").strip()
    oauth_introspection_client_id: str = os.getenv("MEMORYBRIDGE_OAUTH_INTROSPECTION_CLIENT_ID", "").strip()
    oauth_introspection_client_secret: str = os.getenv(
        "MEMORYBRIDGE_OAUTH_INTROSPECTION_CLIENT_SECRET", ""
    ).strip()
    oauth_introspection_timeout: int = _int("MEMORYBRIDGE_OAUTH_INTROSPECTION_TIMEOUT", 10)

    embed_base_url: str = os.getenv("MEMORYBRIDGE_EMBED_BASE_URL", "").rstrip("/")
    embed_api_key: str = os.getenv("MEMORYBRIDGE_EMBED_API_KEY", "")
    embed_model: str = os.getenv("MEMORYBRIDGE_EMBED_MODEL", "qwen3-embedding:0.6b")
    embed_dim: int | None = _opt_int("MEMORYBRIDGE_EMBED_DIM")
    embed_timeout: int = _int("MEMORYBRIDGE_EMBED_TIMEOUT", 30)

    lexical_max_points: int = _int("MEMORYBRIDGE_LEXICAL_MAX_POINTS", 5000)
    search_top_k: int = _int("MEMORYBRIDGE_SEARCH_TOP_K", 12)

    worker_interval: int = _int("MEMORYBRIDGE_WORKER_INTERVAL", 5)
    snapshot_interval: int = _int("MEMORYBRIDGE_SNAPSHOT_INTERVAL", 86400)
    snapshot_retention: int = _int("MEMORYBRIDGE_SNAPSHOT_RETENTION", 14)
    archive_dir: Path = Path(os.path.expanduser(os.getenv("MEMORYBRIDGE_ARCHIVE_DIR", "/var/lib/memorybridge/archive")))
    snapshot_collections: tuple[str, ...] = _csv("MEMORYBRIDGE_SNAPSHOT_COLLECTIONS")

    mcp_url: str = os.getenv("MEMORYBRIDGE_MCP_URL", "http://127.0.0.1:8765/mcp")
    mcp_token: str = _client_token()
    spool_dir: Path = Path(os.path.expanduser(os.getenv("MEMORYBRIDGE_SPOOL_DIR", "~/.memorybridge/spool")))

    @property
    def all_source_collections(self) -> tuple[str, ...]:
        out: list[str] = []
        for name in (self.write_collection, *self.source_collections):
            if name and name not in out:
                out.append(name)
        return tuple(out)

    @property
    def all_vector_collections(self) -> tuple[str, ...]:
        # Explicit collections are assumed compatible with the configured embedding.
        # The base fallback name is kept as a legacy compatibility candidate; new
        # MemoryBridge-owned indexes use model+dimension generation names.
        out: list[str] = []
        for name in (*self.vector_collections, self.fallback_collection):
            if name and name not in out:
                out.append(name)
        return tuple(out)

    def fallback_generation(self, dimension: int) -> str:
        model_hash = hashlib.sha256(self.embed_model.encode("utf-8")).hexdigest()[:10]
        return f"{self.fallback_collection}__{model_hash}__{dimension}"

    @property
    def effective_snapshot_collections(self) -> tuple[str, ...]:
        if self.snapshot_collections:
            return self.snapshot_collections
        # Raw/source collections are durable memory. Fallback vectors are disposable
        # derived indexes and are rebuilt automatically, so they are not snapshotted
        # unless explicitly requested via MEMORYBRIDGE_SNAPSHOT_COLLECTIONS.
        return self.all_source_collections

    @property
    def embedding_enabled(self) -> bool:
        return bool(self.embed_base_url and self.embed_model)
