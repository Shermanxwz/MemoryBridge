# Architecture

## Non-negotiable invariants

1. **Capture before intelligence.** A conversation is durably spooled locally before any network/model work.
2. **Store before index.** `memory_put` commits raw payload to Qdrant before optional embedding/indexing.
3. **Native first.** Codex/OpenClaw/Hermes may use their own index. MemoryBridge does not force one retrieval model.
4. **Graceful retrieval degradation.** Agent native -> Qwen/Qdrant vector -> no-model lexical -> raw/recent.
5. **Indexes are disposable.** Fallback collections are generation-scoped by embedding model + dimension and may
   be rebuilt or replaced without altering raw memories.
6. **Snapshots are recoverable, exports are portable.** MemoryBridge-owned archives contain a Qdrant snapshot plus JSONL.GZ and SHA-256 manifest. An external raw-snapshot archive is verified through its own `latest.json` and sidecars.
7. **No user-facing control plane.** No dashboard, policy evolution, ranking governance, Kafka, Redis or graph database.

## Data flow

```text
Codex / OpenClaw / Hermes
        |  local lifecycle hooks
        v
   local atomic spool  ---- retry ----+
                                      |
                                      v
                             MemoryBridge MCP
                                      |
                              durable raw write
                                      v
                                    Qdrant
                                      |
                      +---------------+---------------+
                      |                               |
                optional worker                 snapshot worker
                      |                               |
           qwen3-embedding:0.6b                       v
                      |                     .snapshot + .jsonl.gz
                      v                               |
            fallback vector index                CloudDrive2

The device that runs Codex/Hermes may also be an archive-verification node. It reads a server-created CloudDrive2
archive and can restore into a dedicated temporary Qdrant, but it does not assume that a local `127.0.0.1:6333`
endpoint is the production database.

Retrieval: agent native index -> vector fallback -> lexical fallback -> raw/recent
```

## Why Qdrant remains useful without becoming a lock-in

Qdrant is both the current raw memory store and the current fallback vector index. The MCP schema deliberately
returns semantic records (`id`, `content`, metadata) rather than exposing HNSW/index internals. The archive's
JSONL.GZ export is the escape hatch if Qdrant or its snapshot format is replaced in the future.
