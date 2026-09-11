# MemoryBridge

## 中文简介

MemoryBridge 是一个基于 MCP 的持久化 AI 记忆与恢复层。它使用 Qdrant 保存原始记忆，通过本地原子 Spool
和后台重试应对网络或服务中断；Worker 异步构建按模型与维度隔离的向量索引，检索按“向量 → 词法 →
原始/最近记录”逐级降级。源数据通过已验证的 Qdrant 快照、JSONL.GZ 导出和 SHA-256 清单归档到
CloudDrive2。

MemoryBridge 连接 ChatGPT、Codex、Hermes 和 OpenClaw，同时保留各宿主自己的原生记忆与索引能力，为多个
AI 客户端提供可恢复、可重建的统一记忆基础设施。

## English overview

MemoryBridge is a durable AI memory and recovery layer built on MCP. It stores source memories in Qdrant and uses
crash-safe local spooling with background retries to tolerate network and service failures. A worker asynchronously
builds generation-scoped vector indexes, while retrieval degrades from vector search to deterministic lexical and
raw/recent reads. Source data is archived to CloudDrive2 as verified Qdrant snapshots, portable JSONL.GZ exports,
and SHA-256 manifests.

MemoryBridge connects ChatGPT, Codex, Hermes, and OpenClaw while preserving each host's native memory and index
capabilities, providing a recoverable and rebuildable shared memory fabric for multiple AI agents.

> **每条源记忆都可恢复；每个派生索引都可重建。**
>
> **Every source record is recoverable; every derived index is rebuildable.** Capture claims are host-specific and tested.

## Supported surfaces

| Surface | MCP read | MCP write | Passive/native capture | Product status |
|---|---:|---:|---:|---|
| ChatGPT Chat | Yes, where custom apps are supported | Where full MCP/write is supported and allowed | No | **official memory-enabled** |
| ChatGPT Work via Plugin + approved app | Yes | Where full MCP/write is supported and allowed | No | **official memory-enabled** |
| Codex | Yes | Yes | Yes | **capture-enabled** |
| Hermes | Yes | Yes | Yes | **capture-enabled** |
| OpenClaw | Yes | Yes | Yes | **capture-enabled** |

`official memory-enabled` and `capture-enabled` are intentionally different claims. Registering an MCP app lets a
host call MemoryBridge tools; it does **not** give MemoryBridge passive access to conversations the host never sends.
ChatGPT currently uses host-invoked app/tool calls rather than a Codex-style per-turn lifecycle hook. The official
ChatGPT integration, Plugin packaging rules and deployment acceptance gates live in
[`integrations/chatgpt/`](integrations/chatgpt/).

## Fixed reliability contract

| Concern | Contract |
|---|---|
| Durable write | raw record first; vector indexing is asynchronous |
| Client outage | local atomic/fsynced spool -> retry later |
| Retrieval | host native index when healthy -> MemoryBridge fallback when needed |
| Fallback search | vector -> lexical -> raw/recent |
| Embedding outage | raw writes continue; indexing remains pending |
| Recovery | live Qdrant -> verified snapshot/archive -> portable JSONL.GZ |
| Derived indexes | disposable and rebuildable |

MemoryBridge intentionally does not add a dashboard, graph database, Kafka/Redis layer, autonomous deletion policy,
or opaque ranking controller to this reliability path.

## Architecture

```text
                               central server
                      +---------------------------+
                      |      MemoryBridge MCP     |
                      | put/get/scan/since/ack    |
                      | recent/status/search      |
                      +-------------+-------------+
                                    |
                                    v
                                live Qdrant
                         durable raw      fallback vectors
                              |                  ^
                              |                  |
                              |        qwen3-embedding:0.6b
                              |        via optional New API
                              |
                              +--> verified snapshots + JSONL.GZ + SHA256

 ChatGPT Chat / Work                    capture-enabled agents
 +-----------------------+             +----------------------------------+
 | approved custom app   |             | Codex | Hermes | OpenClaw       |
 | optional Plugin/Skill |             | native lifecycle adapter         |
 | OAuth 2.1             |             | local fsync spool                |
 +-----------+-----------+             +----------------+-----------------+
             |                                            |
             | host-invoked MCP                           | background retry
             +----------------------+---------------------+
                                    |
                                    v
                              MemoryBridge MCP
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the detailed invariants and data flow.

The raw/source collections are the durable boundary. The worker writes a disposable collection such as
`memorybridge_fallback__<model-hash>__<dimension>`; changing the embedding model or dimension creates a new
generation instead of mixing incompatible vectors. Losing that derived collection temporarily affects vector
retrieval, but does not lose source memories or prevent lexical/raw recovery.

## MCP tool contract

MemoryBridge exposes exactly eight base host-facing tools. The ChatGPT-only deployment can opt in to two additional
MCP Apps UI tools without changing the legacy endpoint:

- `memory_put` — append durable context; state-changing, non-destructive.
- `memory_scan` — page raw durable memories for import/bootstrap.
- `memory_since` — consume MemoryBridge-owned writes after a cursor.
- `memory_get` — fetch one raw memory by id.
- `memory_recent` — model-free recent-memory fallback.
- `memory_ack` — advance a sync consumer cursor; state-changing, non-destructive and idempotent.
- `memory_status` — health, counts, stream lag, archive and fallback state.
- `memory_search` — vector search with deterministic lexical/raw degradation.

When `MEMORYBRIDGE_CHATGPT_UI=true` is set only on the ChatGPT instance, it additionally exposes:

- `memorybridge_archive_panel` — persist a prepared summary and render the authoritative result card.
- `memorybridge_archive_save` — compatibility/model fallback for clients that cannot render the archive card.

The explicit ChatGPT archive command writes the prepared summary in the same `memorybridge_archive_panel` call;
the card is result-only and no longer depends on a second component-side `tools/call`. It does not send a follow-up
chat prompt.

Tool names, schemas and safety annotations are a compatibility surface. The ChatGPT seal workflow validates them
both in-process and over the packaged server's authenticated Streamable HTTP wire path. The archive-card tools are
opt-in and do not appear on the legacy Codex/Hermes/OpenClaw surface.

## Authentication

MemoryBridge supports two **mutually exclusive** server-side bearer verification modes.

### Operator-managed static bearer tokens

Use distinct device tokens for trusted Codex/Hermes/OpenClaw clients:

```env
MEMORYBRIDGE_BEARER_TOKENS=long-random-token-device-a,long-random-token-device-b
MEMORYBRIDGE_AUTH_REQUIRED_SCOPES=memory
```

Do not put tokens in Git, chat messages, process arguments, or shell history. Interactive clients can use the
owner-only token-file flow documented below.

### OAuth resource-server mode for ChatGPT

For the official ChatGPT custom-app path, leave static bearer tokens empty and configure an external OAuth/OIDC
authorization server plus RFC 7662 introspection:

```env
MEMORYBRIDGE_PUBLIC_MCP_URL=https://memory.example.com/mcp
MEMORYBRIDGE_AUTH_ISSUER=https://auth.example.com
MEMORYBRIDGE_AUTH_REQUIRED_SCOPES=memory
MEMORYBRIDGE_OAUTH_INTROSPECTION_URL=https://auth.example.com/oauth2/introspect
MEMORYBRIDGE_OAUTH_INTROSPECTION_CLIENT_ID=memorybridge-resource-server
MEMORYBRIDGE_OAUTH_INTROSPECTION_CLIENT_SECRET=...
```

OAuth mode fails closed on inactive tokens, missing/mismatched configured issuer, missing/mismatched MCP
resource/audience, expiry at the MCP auth layer, or missing required scope. Enabling OAuth and static server tokens
at the same time is rejected rather than silently falling back.

Production ChatGPT setup and the external workspace seal checklist are documented in
[`integrations/chatgpt/README.md`](integrations/chatgpt/README.md) and
[`integrations/chatgpt/DEPLOYMENT_SEAL.md`](integrations/chatgpt/DEPLOYMENT_SEAL.md).

## Quick server deployment

```bash
cp .env.example .env
$EDITOR .env

# The production container runs as uid/gid 10001.
sudo install -d -o 10001 -g 10001 "$(grep '^MEMORYBRIDGE_ARCHIVE_DIR=' .env | cut -d= -f2-)"

docker compose up -d --build
```

Recommended topology:

```text
Internet -> HTTPS reverse proxy :443 -> MemoryBridge :8765 -> host-local Qdrant :6333
                                                     -> optional embedding gateway
CloudDrive2 / archive mount <------------------------- archive worker
```

Do **not** expose Qdrant publicly. When the reverse proxy and MemoryBridge share a host, keep
`MEMORYBRIDGE_HOST=127.0.0.1` and expose only the TLS reverse-proxy endpoint.

## Capture-enabled client enrollment

Capture adapters never perform network I/O inside a host lifecycle hook. They fsync a small owner-only local record;
`memorybridge spool-sync --daemon` delivers it in the background with retry/backoff.

Create the local client credential files:

```bash
install -d -m 700 ~/.config ~/.memorybridge/spool
install -m 600 /dev/null ~/.config/memorybridge.token
# Write only the issued device token to ~/.config/memorybridge.token.
cat > ~/.config/memorybridge.env <<'EOF'
MEMORYBRIDGE_MCP_URL=https://memory.example.com/mcp
MEMORYBRIDGE_MCP_TOKEN_FILE=~/.config/memorybridge.token
MEMORYBRIDGE_SPOOL_DIR=~/.memorybridge/spool
EOF
chmod 600 ~/.config/memorybridge.env
```

The included systemd user unit can run the spool daemon continuously:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/memorybridge-spool.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now memorybridge-spool.service
```

Integration-specific adapters:

- [`integrations/codex/`](integrations/codex/) — `UserPromptSubmit` + `Stop` local fsync and `SessionEnd` transcript reconciliation.
- [`integrations/hermes/`](integrations/hermes/) — native `on_session_finalize` reading persisted Hermes `SessionDB`.
- [`integrations/openclaw/`](integrations/openclaw/) — typed local capture plugin plus remote MCP configuration.
- [`integrations/chatgpt/`](integrations/chatgpt/) — official remote MCP app + OAuth + optional Plugin/Skill; **not passive capture**.

MCP registration without a native lifecycle adapter is tool connectivity, not automatic capture.

## Codex compatibility note

For Codex 0.151.0 and newer, use `bearer_token_env_var` rather than returning `Authorization` from a header helper:

```toml
[mcp_servers.memorybridge]
url = "https://memory.example.com/mcp"
bearer_token_env_var = "MEMORYBRIDGE_MCP_TOKEN"
required = false
```

The repository includes the systemd app-server wrapper and regression coverage for this boundary. The older header
helper remains only for older Codex clients that permit that flow.

## Optional embedding fallback

Configure an OpenAI-compatible embedding endpoint only when vector fallback is desired:

```env
MEMORYBRIDGE_EMBED_BASE_URL=https://your-new-api/v1
MEMORYBRIDGE_EMBED_API_KEY=...
MEMORYBRIDGE_EMBED_MODEL=qwen3-embedding:0.6b
MEMORYBRIDGE_EMBED_DIM=1024
```

A model/dimension change creates a new generation-scoped fallback collection instead of mixing incompatible vector
spaces. Embedding failure never blocks durable raw writes, lexical retrieval, raw/recent retrieval, or archive
recovery.

`qwen3:4b-instruct` is intentionally not on the reliability path. Analysis/dedup/summarization may be added by a
caller as optional curation, but no language-model outage may prevent capture, storage or recovery.

## Optional daily conversation summaries

本仓库提供一个独立的每日摘要任务：它默认在本地时间每天 03:40 处理前一天及最近 7 天内尚未完成的 Codex 会话，并读取
MemoryBridge 原始集合中由 Hermes/OpenClaw/Codex 捕获的消息；使用 OpenAI-compatible New API 的
`qwen3:4b-instruct` 生成摘要，摘要先写入 owner-only 本地 spool，再由现有 spool synchronizer 和 worker
写入 `memorybridge_raw`、建立向量索引并进入既有备份链路。任务按日期/会话使用幂等键，重复运行不会重复写入。

该任务不读取 ChatGPT 云端历史，也不会把完整 transcript 写进 MemoryBridge；ChatGPT 仍须通过明确的归档工具
调用提供摘要。摘要器是可选派生层，模型不可用时不会阻塞任何原始捕获、MCP 写入、索引或恢复流程。

部署单元位于 [`deploy/systemd/memorybridge-daily-summary.service`](deploy/systemd/memorybridge-daily-summary.service)
和 [`deploy/systemd/memorybridge-daily-summary.timer`](deploy/systemd/memorybridge-daily-summary.timer)。可用
`memorybridge-daily-summary --date YYYY-MM-DD --dry-run` 只读检查目标日期的会话范围。

### Optional daily conversation summaries (English)

The repository also ships an isolated daily summarizer. By default, at 03:40 local time it processes the previous
day and retries unfinished dates from the preceding week, covering persisted Codex conversations and messages captured from Hermes/OpenClaw/Codex in the MemoryBridge raw
collection. It calls the OpenAI-compatible New API model `qwen3:4b-instruct`, writes only a derived summary to the
owner-only local spool, and lets the existing spool synchronizer and worker persist, index, and back it up. Date/session
idempotency keys make retries safe.

It does not scrape ChatGPT cloud history and does not copy full transcripts into MemoryBridge; ChatGPT still supplies
summaries through an explicit archive tool call. The summarizer is an optional derived layer, so model downtime never
blocks capture, MCP writes, indexing, or recovery. Use `memorybridge-daily-summary --date YYYY-MM-DD --dry-run` for a
read-only scope check.

## Archive and recovery

Derived vector indexes are rebuildable. Durable source collections are archived independently. The repository
supports both MemoryBridge-native archive manifests and verification of the external Qdrant snapshot + SHA-256
layout used by the operator deployment.

Core commands:

```bash
memorybridge snapshot-create --collection memorybridge_raw
memorybridge archive-verify /path/to/archive/...manifest.json
memorybridge restore-latest memorybridge_raw --force

memorybridge deployment-seal \
  --archive-dir /opt/memorybridge-archive/memorybridge_raw \
  --archive-only
```

For a destructive recovery drill, use a **dedicated temporary Qdrant**, never production:

```bash
memorybridge deployment-seal \
  --archive-dir /opt/memorybridge-archive/memorybridge_raw \
  --drill-qdrant-url http://127.0.0.1:16333 \
  --latest-only
```

The deployment seal generates/accepts only its isolated `__memorybridge_seal_...` collection namespace and verifies
cleanup afterward. Archive verification checks bytes/hashes, retained snapshot structure, permission boundaries and,
when requested, freshness. A timestamp proves the retained artifact's time; it does not magically prove every future
scheduled backup will succeed.

The additive CloudDrive2 backup installer remains available for the operator topology:

```bash
sudo deploy/install-memorybridge-cloud-backup.sh
sudo systemctl start memorybridge-cloud-backup.service
```

## Operating commands

Normal operation requires no dashboard and no manual memory management. Engineering/operator commands are kept
small:

```bash
memorybridge status
memorybridge index-once
memorybridge spool-sync
memorybridge snapshot-create
memorybridge archive-verify <manifest>
memorybridge restore-latest <collection> --force
memorybridge deployment-seal --archive-dir /path/to/archive --archive-only
```

## Failure behavior

| Failure | Defined behavior |
|---|---|
| client MCP/network unavailable | owner-only local spool retains records and retries |
| malformed spool/transcript job | quarantined without blocking later valid records |
| embedding provider unavailable | raw writes continue; vector indexing remains pending |
| vector search unavailable | lexical -> raw/recent |
| host native index unavailable | `memory_search` is the bounded server fallback |
| Qdrant restart | clients continue spooling; service resumes after Qdrant returns |
| live source collection lost | verified archive restore; portable JSONL.GZ remains readable |
| invalid OAuth/static auth | fail closed before tool execution |

## Seal model

The repository does not use “sealed” as a marketing synonym for “implemented”. A repository seal requires
reproducible CI, real protocol/container contracts, recovery drills where applicable, pinned upstream compatibility
contracts, and an explicit boundary for infrastructure that public CI cannot control.

- [`SEAL_REPORT.md`](SEAL_REPORT.md) records the existing core/Codex/Hermes/OpenClaw deployment seal.
- `.github/workflows/ci.yml` runs the supported Python matrix.
- `.github/workflows/seal.yml` exercises the production container, real Qdrant recovery and native-agent contracts.
- `.github/workflows/chatgpt-seal.yml` exercises ChatGPT-facing MCP/OAuth/Plugin contracts and the packaged HTTP wire.
- [`integrations/chatgpt/DEPLOYMENT_SEAL.md`](integrations/chatgpt/DEPLOYMENT_SEAL.md) defines the external ChatGPT workspace acceptance gates that repository CI cannot impersonate.

A real ChatGPT deployment is not called `SEALED` until its workspace admin completes those external gates against the
intended OAuth provider, approved app, Plugin and Chat/Work surfaces.

## Scope and freeze rule

MemoryBridge is a **memory source and recovery fabric**, not a universal memory-intelligence layer. Durable records
are the product; models, vector indexes, agent adapters and even MCP are replaceable integration layers around them.

After a sealed release, changes should be limited to upstream compatibility, integrity/recovery, security, and tests
or documentation needed to prove those fixes. New product capabilities should explicitly reopen the feature surface
instead of being hidden inside maintenance.

See [`ARCHITECTURE.md`](ARCHITECTURE.md), [`SECURITY.md`](SECURITY.md), [`PRIVACY.md`](PRIVACY.md) and
[`TERMS.md`](TERMS.md).
