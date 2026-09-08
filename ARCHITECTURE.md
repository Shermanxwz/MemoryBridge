# Architecture

## Non-negotiable invariants

1. **Capture before intelligence.** A capture-enabled host durably spools a conversation locally before network/model work.
2. **Store before index.** `memory_put` commits raw payload to Qdrant before optional embedding/indexing.
3. **Native first where a host exposes one.** Codex/OpenClaw/Hermes may use their own index; MemoryBridge does not force one retrieval model.
4. **Tool access is not lifecycle capture.** ChatGPT Chat/Work can officially read and write through the MCP app, but MCP does not grant passive access to every conversation turn.
5. **Graceful retrieval degradation.** Agent native -> Qwen/Qdrant vector -> no-model lexical -> raw/recent.
6. **Indexes are disposable.** Fallback collections are generation-scoped by embedding model + dimension and may be rebuilt without altering raw memories.
7. **Snapshots are recoverable, exports are portable.** MemoryBridge-owned archives contain a Qdrant snapshot plus JSONL.GZ and SHA-256 manifest. External raw-snapshot archives are verified through their own `latest.json` and sidecars.
8. **No user-facing control plane.** No dashboard, policy evolution, ranking governance, Kafka, Redis or graph database.

## Data flow

```text
                           official host surfaces

 ChatGPT Chat / Work --------------------------------------------+
 custom MCP app / Plugin                                         |
 host-invoked read/write tools                                   |
                                                                  v
 Codex / OpenClaw / Hermes                              +--------------------+
        | native lifecycle hooks                         |  MemoryBridge MCP  |
        v                                                | Streamable HTTP    |
   local atomic spool  -------- background retry ------>| OAuth/static auth  |
                                                        +---------+----------+
                                                                  |
                                                        durable raw write
                                                                  v
                                                               Qdrant
                                                                  |
                                                 +----------------+----------------+
                                                 |                                 |
                                           optional worker                   snapshot worker
                                                 |                                 |
                                      qwen3-embedding:0.6b                         v
                                                 |                       .snapshot + .jsonl.gz
                                                 v                                 |
                                       fallback vector index                  CloudDrive2
```

The ChatGPT path is deliberately different from the capture-enabled clients. ChatGPT invokes MemoryBridge as an official remote MCP app when the host chooses/permits a tool call. MemoryBridge does not claim a `UserPromptSubmit`, `Stop`, `SessionEnd`, or equivalent ChatGPT lifecycle event that OpenAI has not exposed to custom MCP apps.

For production ChatGPT OAuth, MemoryBridge is the OAuth resource server. A separate authorization server performs user authorization and token issuance; MemoryBridge publishes the MCP protected-resource metadata through the MCP SDK and verifies bearer tokens either with its existing static-token mode or, for OAuth deployments, through RFC 7662 token introspection. The two verifier modes are mutually exclusive.

The device that runs Codex/Hermes may also be an archive-verification node. It reads a server-created CloudDrive2 archive and can restore into a dedicated temporary Qdrant, but it does not assume that a local `127.0.0.1:6333` endpoint is the production database.

Retrieval for capture-enabled clients: agent native index -> vector fallback -> lexical fallback -> raw/recent.

Retrieval for ChatGPT: `memory_search` -> vector fallback -> lexical fallback -> raw/recent, with `memory_get`/`memory_recent` for explicit follow-up reads.

## Why Qdrant remains useful without becoming a lock-in

Qdrant is both the current raw memory store and the current fallback vector index. The MCP schema deliberately returns semantic records (`id`, `content`, metadata) rather than exposing HNSW/index internals. The archive's JSONL.GZ export is the escape hatch if Qdrant or its snapshot format is replaced in the future.
