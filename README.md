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
                          +--> verified snapshot + JSONL.GZ + SHA256 --> CloudDrive2

  Codex -------------------+       OpenClaw ----------------+       Hermes ----------------+
  native index first       |       native index first      |       native index first     |
  turn hooks + reconcile   |       typed hooks -> spool    |       SessionDB finalize     |
             local fsync --+-------------------------------+-------------------------------+
                                        |
                                  background retry
                                        |
                                        v
                                  MemoryBridge MCP
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

## What "connected" means

Registering an MCP URL gives an agent access to MemoryBridge tools; it does **not** let the server observe or
capture conversations that the host agent never sends. A client is capture-enabled only when all four links exist:

1. the agent's native lifecycle adapter writes each turn or finalized session;
2. the adapter fsyncs an owner-only local spool without doing network work in the hook;
3. a background spool daemon delivers pending records to authenticated MCP; and
4. the server's worker asynchronously indexes stored raw records.

`memory_put` is the durable write boundary. It commits raw data first, then the worker builds the configured
generation-scoped vector index. If New API or Qdrant indexing is unavailable, the raw record remains durable and
search falls back to lexical/raw retrieval.

## Why the local model still matters

Configure your New API/OpenAI-compatible endpoint:

```env
MEMORYBRIDGE_EMBED_BASE_URL=https://your-new-api/v1
MEMORYBRIDGE_EMBED_API_KEY=...
MEMORYBRIDGE_EMBED_MODEL=qwen3-embedding:0.6b
MEMORYBRIDGE_EMBED_DIM=1024
```

In the current deployment, `TYC-Memory-Embedding` is the New API channel label; the model sent to the
OpenAI-compatible `/embeddings` endpoint is `qwen3-embedding:0.6b`, which returns 1024-dimensional vectors. The
`TYC-Memory-Analysis` channel is not on MemoryBridge's reliability path; it may be used by an agent or curator for
optional analysis without affecting capture, storage or recovery.

The worker asynchronously builds a generation-scoped fallback collection such as
`memorybridge_fallback__<model-hash>__<dimension>`. A model/dimension change therefore creates a fresh disposable
index instead of corrupting an old embedding space. If the model device is offline, raw writes continue and remain
`index_status=pending`; the worker catches up automatically when the model returns. Search degrades to lexical/raw.

You may also point `MEMORYBRIDGE_VECTOR_COLLECTIONS` at existing Qdrant vector collections whose vectors are
compatible with the configured embedding model. This lets MemoryBridge use a current snapshot/index without
forcing a migration.

Do not list an existing collection with a different dimension as a vector candidate. In the current deployment,
the legacy `sherman_memory` vectors are 768-dimensional, so they remain a source/lexical collection while new
MemoryBridge writes use the 1024-dimensional generation-scoped fallback index.

## Quick deployment

```bash
cp .env.example .env
$EDITOR .env

# The container runs as uid/gid 10001. A bind-mounted archive path must be writable by it.
# For a normal local filesystem, pre-create it before the first compose start:
sudo install -d -o 10001 -g 10001 "$(grep '^MEMORYBRIDGE_ARCHIVE_DIR=' .env | cut -d= -f2-)"

docker compose up -d --build
```

If the archive path is a CloudDrive2/FUSE mount that does not support `chown`, configure that mount so uid/gid
`10001` can create, rename and fsync files in the archive directory before starting the worker.

Recommended production topology:

```text
Internet -> HTTPS reverse proxy :443 -> MemoryBridge :8765 -> 127.0.0.1:6333 Qdrant
                                                     -> New API model node (optional)
CloudDrive2 mount <---------------- archive worker
```

Do **not** expose Qdrant publicly. Keep `MEMORYBRIDGE_HOST=127.0.0.1` when the HTTPS reverse proxy runs on the same
host. Static `MEMORYBRIDGE_BEARER_TOKENS` are intended for a single trusted operator; use a distinct token per
device and rotate a leaked token immediately.

The worker is intentionally boring: it retries pending vector indexing and periodically creates archives. Point
`MEMORYBRIDGE_ARCHIVE_DIR` at a CloudDrive2-mounted directory if desired.

The operator's existing Qdrant backup may cover a separate pre-existing collection. Install the additive host
backup unit below so MemoryBridge's own durable `memorybridge_raw` records and `memorybridge_meta` sequence/ack
metadata are archived to the same CloudDrive2 tree without changing that existing job:

```bash
sudo deploy/install-memorybridge-cloud-backup.sh
sudo systemctl start memorybridge-cloud-backup.service  # optional immediate first run
```

It runs daily at 03:20, after the existing 03:15 job, and writes one independently verifiable archive under
`qdrant-memory-backup/memorybridge_raw/` and `qdrant-memory-backup/memorybridge_meta/`. Each run uploads the
snapshot through the CloudDrive2 container, verifies the remote SHA-256 and sidecar, atomically advances that
collection's `latest.json`, then removes only the newly created local Qdrant snapshot. A failed upload leaves the
source snapshot available for retry and returns a failed systemd result.

## Sealed archive format

For each raw/source collection the worker creates (derived fallback-vector indexes are rebuildable and are not
archived by default):

```text
archive/<collection>/
  <qdrant-name>.snapshot
  <qdrant-name>.snapshot.jsonl.gz
  <qdrant-name>.snapshot.manifest.json
```

An archive is not accepted merely because Qdrant returned a snapshot filename. MemoryBridge immediately restores
the new snapshot into a disposable verification collection, reads the restored records back, computes a semantic
fingerprint, and generates the portable `JSONL.GZ` from that restored copy. Only then is the manifest finalized.
The manifest also contains SHA-256 hashes. Restore verifies the files and semantic fingerprint and refuses to
overwrite a live collection unless `--force` is explicit.

```bash
memorybridge snapshot-create --collection memorybridge_raw
memorybridge archive-verify /path/to/archive/...manifest.json
memorybridge restore-latest memorybridge_raw --force
```

### Client/archive verification node

When the server's backup job writes raw Qdrant snapshots to a CloudDrive2/FUSE mount, the client device can verify
that external format without pretending to be the Qdrant host:

For a strict client-side permission boundary, add a dedicated read-only CloudDrive2 mount for the backup subtree.
CloudDrive2's Linux advanced mount settings support an explicit UID, GID and permission mode; keep the broad
interactive mount separate from this verification mount:

```toml
[[mount_points]]
name = "MemoryBridge archive verifier"
source_path = "/115open/qdrant-memory-backup"
mount_point = "/opt/memorybridge-archive"
read_only = true
uid = 0
gid = 0
permission = "0700"
local_mount = false
auto_mount = true
```

Then verify each collection directory independently:

```bash
memorybridge deployment-seal \
  --archive-dir /opt/memorybridge-archive/memorybridge_raw \
  --archive-only

memorybridge deployment-seal \
  --archive-dir /opt/memorybridge-archive/memorybridge_meta \
  --archive-only
```

This read-only check validates `latest.json`, every retained `.snapshot` against its `.sha256` sidecar, the latest
snapshot tar structure, the recorded 03:00-hour timestamp, retention inventory and owner-only permissions. The
timestamp check proves the time of a retained artifact; it cannot prove that the server's daily job never missed a
run. Add `--max-age-hours 30` when freshness is part of the operator's policy. The ordinary broad CloudDrive2 mount
may still use its interactive permissions; the dedicated verification mount is the one covered by the owner-only
seal gate.

For a destructive recovery drill, point the command at a dedicated temporary Qdrant instance. It generates or
accepts only a `__memorybridge_seal_` collection name, restores the snapshot, reads it back, and confirms cleanup:

```bash
memorybridge deployment-seal \
  --archive-dir /opt/memorybridge-archive/memorybridge_raw \
  --drill-qdrant-url http://127.0.0.1:16333 \
  --latest-only
```

The drill URL is never inferred from the live Qdrant URL. Do not point it at a production Qdrant. The command only
reports full `SEALED` when archive security/integrity, an explicit live Qdrant probe, an explicit read-only MCP
`memory_status` probe and the isolated recovery drill all pass. `python scripts/deployment_seal.py ...` is an
equivalent checkout-local entry point.

## Zero-friction capture

The important rule is **never perform network work inside a host-agent shutdown/message hook**. Hooks only fsync a
small local file. A background spool daemon performs MCP delivery with exponential backoff.

Run on each client device as a background process, or install the included user service:

```bash
memorybridge spool-sync --daemon
```

For the included systemd user unit, store persistent client settings in `~/.config/memorybridge.env`:

```env
MEMORYBRIDGE_MCP_URL=https://memory.example.com/mcp
MEMORYBRIDGE_MCP_TOKEN=YOUR_DEVICE_TOKEN
MEMORYBRIDGE_SPOOL_DIR=~/.memorybridge/spool
```

Then install and enable it:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/memorybridge-spool.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now memorybridge-spool.service
```

### Self-serve client enrollment

The URL alone is not a credential and is never sufficient for a client enrollment. The endpoint requires HTTPS and
a bearer token; possession of a token authorizes MemoryBridge reads and writes, so issue one distinct token per
device and rotate it immediately if exposed. Do not put a token in a repository, shell history, process argument, or
chat message.

After installing this package in the same environment as the host agent, put the device token in an owner-only file
and create the client environment file:

```bash
install -d -m 700 ~/.config ~/.memorybridge/spool
install -m 600 /dev/null ~/.config/memorybridge.token
# Edit ~/.config/memorybridge.token with the device token, then:
cat > ~/.config/memorybridge.env <<'EOF'
MEMORYBRIDGE_MCP_URL=https://api.example.com/memorybridge/mcp
MEMORYBRIDGE_MCP_TOKEN_FILE=~/.config/memorybridge.token
MEMORYBRIDGE_SPOOL_DIR=~/.memorybridge/spool
EOF
chmod 600 ~/.config/memorybridge.env
```

For Codex, configure the MCP server and merge `integrations/codex/hooks.json.example` into `~/.codex/hooks.json`
without replacing unrelated hooks. The current Codex CLI form is:

```bash
codex mcp add memorybridge \
  --url https://api.example.com/memorybridge/mcp \
  --bearer-token-env-var MEMORYBRIDGE_MCP_TOKEN
codex mcp list
```

For Codex 0.151.0 and newer, use `bearer_token_env_var` rather than a header helper. The current Codex app server
rejects `Authorization` returned by `http_headers_helper` as a reserved header, which leaves the server enabled in
configuration but absent from the live tool catalog:

```toml
[mcp_servers.memorybridge]
url = "https://api.example.com/memorybridge/mcp"
bearer_token_env_var = "MEMORYBRIDGE_MCP_TOKEN"
required = false
```

For a systemd-managed Codex Web app server, install `scripts/memorybridge_codex_app_server.sh` as
`/usr/local/libexec/memorybridge-codex-app-server` and install
`deploy/systemd/codex-official-app-server-memorybridge.conf` as a drop-in for the active app-server unit. The
wrapper reads the owner-only token file and exports the value only to the Codex process. For a one-shot CLI check:

```bash
MEMORYBRIDGE_MCP_TOKEN="$(tr -d '\r\n' < ~/.config/memorybridge.token)" codex mcp list
```

The wrapper resolves `codex` from the service `PATH`; set `MEMORYBRIDGE_CODEX_BIN` in the service drop-in when the
installed executable is outside that path.

`codex mcp list` verifies the registration; the Web app-server smoke test should call its official
`mcpServerStatus/list` method and confirm that `memorybridge` exposes tools.

The older `scripts/memorybridge_codex_headers.py` helper is retained only for older Codex clients that accept an
Authorization header from a helper; do not use it with Codex 0.151.0 or newer.

For Hermes, install the package in Hermes' Python environment, copy `integrations/hermes/` to
`~/.hermes/plugins/memorybridge/`, enable it, and add the remote MCP server to `~/.hermes/config.yaml`. Keep the
token in `~/.hermes/.env` with mode `0600`:

```yaml
mcp_servers:
  memorybridge:
    url: https://api.example.com/memorybridge/mcp
    headers:
      Authorization: "Bearer ${MCP_MEMORYBRIDGE_API_KEY}"
    enabled: true
```

Then validate both halves:

```bash
hermes plugins doctor ~/.hermes/plugins/memorybridge --ci
hermes mcp test memorybridge
memorybridge status
```

MCP registration without the native hook/plugin is a tool-connected client, not an automatically captured client.
OpenClaw follows the same rule: install its typed capture plugin and run its MCP probe. The server cannot make an
arbitrary MCP consumer auto-capture because MCP has no access to that consumer's conversation lifecycle.

Integration examples live in `integrations/`:

- `integrations/codex/` — native `UserPromptSubmit` + `Stop` local fsync and `SessionEnd` persisted-transcript reconciliation.
- `integrations/openclaw/` — typed `message_received` + `agent_end` local capture plugin and remote MCP configuration.
- `integrations/hermes/` — native `on_session_finalize` hook reading Hermes' already-persisted `SessionDB` and locally spooling it.

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
memorybridge deployment-seal --archive-dir /path/to/external-snapshot-archive --archive-only
```

## Failure behavior

| Failure | Behavior |
|---|---|
| MCP/network unavailable on a client | local spool keeps records and retries later |
| malformed local spool record | quarantined; later valid records continue |
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
