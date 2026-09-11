# Architecture

## 中文概览

MemoryBridge 的核心路径是“先捕获、后智能；先存储、后索引”。客户端生命周期适配器先把记录以本地
fsync Spool 持久化，再由后台进程通过认证 MCP 重试投递。MemoryBridge 将原始记录写入 Qdrant 后才进行
异步嵌入和向量索引；向量不可用时，检索自动降级到词法匹配和原始/最近记录。原始集合是恢复边界，
向量集合是可丢弃、可重建的派生数据。

## English overview

MemoryBridge follows a capture-before-intelligence and store-before-index contract. Client lifecycle adapters first
persist records to a local fsync spool; a background process delivers them through authenticated MCP with retries.
MemoryBridge commits the raw record to Qdrant before asynchronous embedding and vector indexing. If vectors are
unavailable, retrieval degrades to lexical and raw/recent reads. Source collections are the recovery boundary;
vector collections are disposable, generation-scoped derived data.

## Non-negotiable invariants

1. **Capture before intelligence.** A capture-enabled host durably spools a conversation locally before network/model work.
2. **Store before index.** `memory_put` commits raw payload to Qdrant before optional embedding/indexing.
3. **Native first where a host exposes one.** Codex, Hermes and OpenClaw may use their own index; MemoryBridge does not force one retrieval model.
4. **Tool access is not lifecycle capture.** ChatGPT Chat/Work can officially read and write through the MCP app, but MCP does not grant passive access to every conversation turn.
5. **Graceful retrieval degradation.** Agent native -> Qwen/Qdrant vector -> no-model lexical -> raw/recent.
6. **Indexes are disposable.** Fallback collections are generation-scoped by embedding model + dimension and may be rebuilt without altering raw memories.
7. **Snapshots are recoverable, exports are portable.** MemoryBridge-owned archives contain a Qdrant snapshot plus JSONL.GZ and SHA-256 manifest. External raw-snapshot archives are verified through their own `latest.json` and sidecars.
8. **One sealed durable writer process.** The packaged production topology uses one MemoryBridge MCP service process for a write collection. It serializes the first-writer idempotency check + sequence allocation + raw upsert. Active-active multi-process/multi-replica writers require an external distributed compare-and-set/serialization mechanism and are outside this seal.
9. **No user-facing control plane.** No dashboard, policy evolution, ranking governance, Kafka, Redis or graph database.

## Data flow

```text
                           official host surfaces

 ChatGPT Chat / Work --------------------------------------------+
 custom MCP app / Plugin                                         |
 host-invoked read/write tools                                   |
                                                                  v
 Codex / Hermes / OpenClaw                              +--------------------+
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

For production ChatGPT OAuth, MemoryBridge is the OAuth resource server. A separate authorization server performs user authorization and token issuance; MemoryBridge publishes the MCP protected-resource metadata through the MCP SDK and verifies bearer tokens either with its existing static-token mode or, for OAuth deployments, through RFC 7662 token introspection. The two verifier modes are mutually exclusive. A configured OAuth issuer is compared exactly and the access token must be bound to the MCP resource/audience before the SDK enforces expiry and required scope.

The service-level `memory_put` critical section makes deterministic idempotency first-writer-wins within the sealed single-process topology. The sequence clock remains durable in Qdrant metadata. Running multiple independent MemoryBridge writers against one raw collection is intentionally not presented as sealed because an in-process lock cannot provide cross-process compare-and-set semantics.

A client device that runs Codex, Hermes or OpenClaw may also be an archive-verification node. It reads a
server-created CloudDrive2 archive and can restore into a dedicated temporary Qdrant, but it does not assume that a
local `127.0.0.1:6333` endpoint is the production database.

Retrieval for capture-enabled clients: agent native index -> vector fallback -> lexical fallback -> raw/recent.

Retrieval for ChatGPT: `memory_search` -> vector fallback -> lexical fallback -> raw/recent, with `memory_get`/`memory_recent` for explicit follow-up reads.

## Current deployment contract

The durable Qdrant source collections (`memorybridge_raw` and `memorybridge_meta`) are archived independently with
verified snapshots, portable JSONL.GZ exports and SHA-256 sidecars. A worker asynchronously indexes pending source
records into a generation-scoped fallback collection such as
`memorybridge_fallback__<model-hash>__<dimension>`. A model or dimension change creates a fresh collection and does
not corrupt an older embedding space.

An existing collection is eligible for vector retrieval only when its dimension and embedding space match the active
configuration. A legacy collection with an incompatible dimension remains a source/lexical collection and must not be
listed as a vector candidate. This separation keeps raw memory durable while allowing vector indexes to be rebuilt or
replaced independently.

## Why Qdrant remains useful without becoming a lock-in

Qdrant is both the current raw memory store and the current fallback vector index. The MCP schema deliberately returns semantic records (`id`, `content`, metadata) rather than exposing HNSW/index internals. The archive's JSONL.GZ export is the escape hatch if Qdrant or its snapshot format is replaced in the future.
