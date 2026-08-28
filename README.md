# MemoryBridge

**Invisible, durable memory fabric for AI agents.**

MemoryBridge is deliberately smaller than a "Memory OS". It does not ask the user to manage retrieval policies,
indexes or dashboards. Once connected, Codex, OpenClaw and Hermes can treat it like infrastructure: conversations
are captured to a crash-safe local spool, synced to a central Qdrant-backed MCP memory source, and archived
automatically. Each agent may keep using its own native memory/index. When that native index is absent or
unavailable, MemoryBridge provides a bounded fallback chain.

> **Every conversation is capturable. Every stored record is recoverable. Every index is rebuildable.**

## The fixed fallback contract

| Concern | Order | Status |
|---|---|---|
| Retrieval | Agent native index -> Qwen/Qdrant | **Required** |
| Retrieval | Vector -> lexical | **Built in** |
| Retrieval | -> raw/recent | **Built in** |
| Write | MCP/service unavailable -> local atomic spool -> retry | **Required** |
| Recovery | Live Qdrant -> verified snapshot archive | **Required** |

The project intentionally stops there. No auto-tuned ranking policy, no dashboard, no Redis/Kafka, no graph DB and
no autonomous deletion system.

## Architecture

```text
                             central server
                    +-----------------------------+
                    |       MemoryBridge MCP      |
                    | scan/since/get/put/ack      |
                    | search (fallback only)      |
                    +-------------+---------------+
                                  |
                                  v
                              live Qdrant
                      raw source       fallback vectors
                          |                    ^
                          |                    |
                          |             qwen3-embedding:0.6b
                          |             via New API (optional)
                          |
                          +--> snapshot + JSONL.GZ + SHA256 --> CloudDrive2

  Codex -------------------+       OpenClaw ----------------+       Hermes ----------------+
  native index first       |       native index first      |       native index first     |
  SessionEnd -> local spool+       hooks -> local spool    +       hooks -> local spool   +
             background retry ----------- MCP ----------- background retry
```

`qwen3:4b-instruct` is intentionally **not** on the reliability path. It can later be used as an optional curator
(extraction/dedup/summarization), but a model outage must never prevent capture, storage, sync or raw retrieval.

## MCP tools

Core source/sync tools:

- `memory_put` — durable raw write; indexing happens later.
- `memory_scan` — first import/native-index bootstrap with an opaque cursor.
- `memory_since` — incremental stream for MemoryBridge-owned writes.
- `memory_get` — fetch one record.
- `memory_recent` — model-free last-resort read path.
- `memory_ack` — consumer reports delivered/indexed cursor.
- `memory_status` — head cursor, per-consumer lag in records, pending/indexed counts, archive presence and fallback state.

Optional fallback retrieval:

- `memory_search` — tries Qwen/Qdrant vector search, then deterministic lexical matching, then raw recent memories.

Clients with a healthy native index should prefer it and may never call `memory_search`.

## Why the local model still matters

Configure your New API/OpenAI-compatible endpoint:

```env
MEMORYBRIDGE_EMBED_BASE_URL=https://your-new-api/v1
MEMORYBRIDGE_EMBED_API_KEY=...
MEMORYBRIDGE_EMBED_MODEL=qwen3-embedding:0.6b
```

The worker asynchronously builds a generation-scoped fallback collection such as
`memorybridge_fallback__<model-hash>__<dimension>`. A model/dimension change therefore creates a fresh disposable
index instead of corrupting an old embedding space. If the model device is offline, raw writes continue and remain
`index_status=pending`; the worker catches up automatically when the model returns. Search degrades to lexical/raw.

You may also point `MEMORYBRIDGE_VECTOR_COLLECTIONS` at existing Qdrant vector collections whose vectors are
compatible with the configured embedding model. This lets MemoryBridge use a current snapshot/index without
forcing a migration.

## Quick deployment

```bash
cp .env.example .env
$EDITOR .env

docker compose up -d --build
```

Recommended production topology:

```text
Internet -> HTTPS reverse proxy :443 -> MemoryBridge :8765 -> 127.0.0.1:6333 Qdrant
                                                     -> New API model node (optional)
CloudDrive2 mount <---------------- archive worker
```

Do **not** expose Qdrant publicly.

The worker is intentionally boring: it retries pending vector indexing and periodically creates archives. Point
`MEMORYBRIDGE_ARCHIVE_DIR` at a CloudDrive2-mounted directory if desired.

## Sealed archive format

For each raw/source collection the worker creates (derived fallback-vector indexes are rebuildable and are not
archived by default):

```text
archive/<collection>/
  <qdrant-name>.snapshot
  <qdrant-name>.snapshot.jsonl.gz
  <qdrant-name>.snapshot.manifest.json
```

The Qdrant snapshot is the fast recovery path. `JSONL.GZ` is the future-proof portable copy. The manifest contains
SHA-256 hashes. Restore verifies hashes and refuses to overwrite a live collection unless `--force` is explicit.

```bash
memorybridge snapshot-create --collection memorybridge_raw
memorybridge archive-verify /path/to/archive/...manifest.json
memorybridge restore-latest memorybridge_raw --force
```

## Zero-friction capture

The important rule is **never perform network work inside a host-agent shutdown/message hook**. Hooks only fsync a
small local file. A background spool daemon performs MCP delivery with exponential backoff.

Run on each client device as a background process, or install the included user service:

```bash
memorybridge spool-sync --daemon
# or copy deploy/systemd/memorybridge-spool.service to ~/.config/systemd/user/
# then: systemctl --user enable --now memorybridge-spool.service
```

Set `MEMORYBRIDGE_MCP_URL`, `MEMORYBRIDGE_MCP_TOKEN` and `MEMORYBRIDGE_SPOOL_DIR` in that user's environment.

Integration examples live in `integrations/`:

- `integrations/codex/` — current Codex `SessionEnd` transcript-reference capture (full read happens in the daemon) and remote MCP config.
- `integrations/openclaw/` — typed `message_received` + `agent_end` capture plugin and MCP command.
- `integrations/hermes/` — `post_llm_call` capture plugin and HTTP MCP config.

The integrations are intentionally thin. They capture/sync; they do not duplicate each agent's native memory logic.

## Operating model

Normal operation requires no UI and no manual memory management. Useful engineering commands are deliberately
limited:

```bash
memorybridge status
memorybridge index-once
memorybridge spool-sync
memorybridge snapshot-create
memorybridge archive-verify <manifest>
memorybridge restore-latest <collection> --force
```

## Failure behavior

| Failure | Behavior |
|---|---|
| MCP/network unavailable on a client | local spool keeps records and retries later |
| New API / embedding model unavailable | writes continue; vector indexing stays pending; lexical/raw retrieval works |
| fallback vector collection unavailable | lexical -> raw/recent |
| host agent native index unavailable | `memory_search` provides server fallback |
| Qdrant restart | clients keep spooling; server resumes after Qdrant returns |
| live collection corrupted/lost | verified archive restore; JSONL.GZ remains readable even if snapshot compatibility changes |

## Scope boundary

MemoryBridge is a **memory source and recovery fabric**, not a universal memory intelligence layer. The durable API
returns semantic records rather than Qdrant/HNSW internals. Models, indexes, agents and even MCP itself are treated
as replaceable adapters around durable memory.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [SECURITY.md](SECURITY.md).
