# MemoryBridge

**Invisible, durable memory fabric for ChatGPT and AI agents.**

MemoryBridge is a deliberately small memory and recovery layer built around MCP, Qdrant, crash-safe local spooling,
and verified archives. It gives multiple AI surfaces one durable source of project context without trying to replace
each host's native memory/index or pretending that every host exposes the same conversation lifecycle.

> **Every stored record is recoverable. Every derived index is rebuildable. Capture claims are host-specific and tested.**

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

## MCP tool contract

MemoryBridge exposes exactly eight host-facing tools:

- `memory_put` — append durable context; state-changing, non-destructive.
- `memory_scan` — page raw durable memories for import/bootstrap.
- `memory_since` — consume MemoryBridge-owned writes after a cursor.
- `memory_get` — fetch one raw memory by id.
- `memory_recent` — model-free recent-memory fallback.
- `memory_ack` — advance a sync consumer cursor; state-changing, non-destructive and idempotent.
- `memory_status` — health, counts, stream lag, archive and fallback state.
- `memory_search` — vector search with deterministic lexical/raw degradation.

Tool names, schemas and safety annotations are a compatibility surface. The ChatGPT seal workflow validates them
both in-process and over the packaged server's authenticated Streamable HTTP wire path.

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
