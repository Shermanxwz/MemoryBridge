# MemoryBridge Seal Report

Seal scope: repository behavior that can be reproduced on public CI infrastructure.

Final branch:

`main`

Latest deployed executable/configuration commit recorded below:

`0114751e9ba4168daf33dc340e011a7fbdab80a9`

Latest repository code commit validated by the same CI and seal workflows:

`0114751e9ba4168daf33dc340e011a7fbdab80a9`

The deployed MemoryBridge server image remains `memorybridge:7f1191b88663`; the Codex Web configuration, wrapper,
and repository deployment assets are at the commit above. Its executable content includes the client spool hardening
validated at `6415ad6`.

Executable/configuration baseline certified before this report was added:

`01776b9057de7d8d0a5c4a18c151ba854d169631`

Certification date: 2026-09-06.

## Verdict

**Repository seal: PASS. Operator deployment: NOT_SEALED under the strict deployment gate.**

The executable baseline passed both the ordinary CI matrix and the destructive/contract `seal` workflow. The final
branch commit adds the report/configuration documentation and passed the same workflows again before the repository
was called sealed.

## Reproducible evidence

### CI

GitHub Actions run `34039599661` completed successfully for the Codex Web bearer-auth fix.

Matrix:

- Python 3.11: compile, tests, Ruff — PASS
- Python 3.12: compile, tests, Ruff — PASS
- Python 3.13: compile, tests, Ruff — PASS
- MCP server construction is part of the normal test suite so an SDK/server API mismatch cannot silently survive CI.

### Seal workflow

GitHub Actions run `34039599680` completed successfully for the Codex Web bearer-auth fix.

Certified jobs:

- Production Docker image build and packaged MCP server startup as uid `10001` — PASS
- Qdrant `v1.18.3` destructive E2E — PASS
- Qdrant `v1.19.0` destructive E2E — PASS
- Codex `0.151.0` native capture contract — PASS
- OpenClaw `2026.7.1-2` stable plugin install/load/capture contract — PASS
- OpenClaw `2026.8.1-beta.3` plugin install/load/capture contract — PASS
- Hermes upstream commit `ac6c8028e00d01ee2f299ba7fd03329c7f10382d` plugin doctor + SessionDB capture contract — PASS

### Deployment-node gate

The repository now includes a separate `deployment-seal` command for a device that is a client and archive
verification node rather than the Qdrant host. It recognizes the external backup shape
`latest.json + <snapshot>.snapshot + <snapshot>.snapshot.sha256`, verifies all retained snapshot bytes and the latest
Qdrant tar structure, reports the mount/filesystem and permission boundary, and never assumes that local
`127.0.0.1:6333` is production. An explicit `--drill-qdrant-url` restores only into a generated
`__memorybridge_seal_` collection and confirms deletion afterward.

The CI seal workflow includes a synthetic external-archive contract for this command. A real deployment is sealed
only after running the command against the operator's archive, explicitly probing the live Qdrant and MCP endpoints,
and completing the isolated recovery drill. A retained file timestamp is evidence of that artifact's creation time;
it is not proof that every scheduled daily backup succeeded.

### Operator deployment closure evidence (2026-09-06)

The target deployment was exercised with the current device acting as a client/archive verification node and the
private server acting as the Qdrant/MCP host. The production MemoryBridge container is running the final image
`memorybridge:7f1191b88663` as the non-root `memorybridge` user, with a read-only root filesystem, dropped Linux capabilities, `no-new-privileges`,
host-local Qdrant access, and HTTPS MCP bearer authentication. The public MCP wire path accepted authenticated
initialize, tool discovery, durable put, deterministic duplicate replay, get, since, and lexical fallback search;
the synthetic point was then deleted and its absence confirmed. The production collection count remained unchanged.

The existing server backup timer is configured for 03:15 Asia/Shanghai and its latest observed run succeeded. An
additive `memorybridge-cloud-backup.timer` now runs at 03:20 and archives both `memorybridge_raw` and
`memorybridge_meta` under the existing CloudDrive2 tree. An immediate run verified both local Qdrant snapshot
downloads against Qdrant and CloudDrive2 SHA-256 values, published sidecars and `latest.json` atomically, and
removed only the newly created local snapshots. The client mount read both new archive families with valid hashes,
sidecars, tar structure, and freshness.

For the destructive drill, secure temporary copies of the raw and metadata archives were restored into a dedicated
temporary Qdrant instance and uniquely prefixed collections. The empty raw collection and one-point metadata
collection both restored successfully; both temporary collections were deleted and the production Qdrant was never
used as the drill target. Codex per-turn/SessionEnd capture and Hermes SessionDB finalize capture each traversed
local fsync spool -> authenticated MCP -> Qdrant and were then cleaned up by exact point id. The root-client spool
service is active; its spool directories are owner-only (`0700`) and its pending, sent, failed, and transcript-job
files are owner-only (`0600`), including receipts tightened during service startup.

The New API route was then exercised on the private server with its active token: the channel labelled
`TYC-Memory-Embedding` accepts the request model `qwen3-embedding:0.6b` and returned a 1024-dimensional vector.
The production MemoryBridge image was rebuilt with `MEMORYBRIDGE_EMBED_BASE_URL=http://127.0.0.1:8199/v1`, the
provider model and dimension, and a real temporary raw point was sent through the public MCP endpoint. Its
asynchronous fallback index was created, `memory_search` returned `mode=vector`, and the raw point plus disposable
fallback collection were removed afterward. `TYC-Memory-Analysis` is intentionally not a MemoryBridge dependency.
The pre-existing `sherman_memory` collection remains a source/lexical collection rather than a vector candidate,
because its 768-dimensional vectors are not compatible with the current 1024-dimensional TYC embedding. New
MemoryBridge-owned writes use the generation-scoped 1024-dimensional fallback collection.

The client/archive device now has a separate read-only CloudDrive2 mount for `/115open/qdrant-memory-backup` with
UID/GID 0 and permission `0700`; both `memorybridge_raw` and `memorybridge_meta` are readable there with secure
owner-only modes and valid integrity/freshness checks. The server-side CloudDrive2 mount was also configured with
permission `0700`, with its prior configuration preserved before the change.

The strict operator deployment gate remains `NOT_SEALED` only because the new 03:20 timer has not yet supplied a
naturally scheduled artifact at the time of this report; the immediate run was deliberately outside the schedule
window. The permission gate is now satisfied on the dedicated verification mount. These are deployment-state
boundaries, not repository blockers. Do not change the verifier to ignore the schedule boundary; rerun the seal
command after the first scheduled run.

### Server-side agent enrollment and residue cleanup (2026-09-06)

The private server's own Codex and Hermes clients are now capture-enabled, not merely MCP-tool-enabled. Codex has
the three native local-fsync hooks under `/root/.codex/hooks.json`; Hermes has the enabled native
`on_session_finalize` plugin under `/root/.hermes/plugins/memorybridge/`; and the owner-only root spool daemon is
enabled as `memorybridge-spool-root.service`. The server-side Hermes plugin doctor passed, Hermes MCP discovered all
8 MemoryBridge tools, and both the Codex hook and Hermes `SessionDB` capture contracts passed in isolated temporary
spools.

A disposable Qdrant plus disposable MCP container drill then exercised server Codex hook -> local spool -> authenticated
MCP -> Qdrant -> asynchronous 1024-dimensional fallback index. The test read the record back through MCP, confirmed
index completion, deleted the exact raw point, and confirmed it was absent. The disposable containers and spool were
removed; the production Qdrant remained at its pre-drill counts (`sherman_memory=27866`, `memorybridge_raw=0`,
`memorybridge_meta=1`).

The residue audit removed only stopped MemoryBridge rollback containers, their six unreferenced historical tagged
images, three unreferenced `/tmp` audit files, superseded environment/config backups, and interpreter caches under
the two audited MemoryBridge trees. The active `memorybridge:7f1191b88663` image, current configuration, archive
data, logs, backup staging directory, Qdrant, New API, and unrelated services were retained. No global Docker prune
was run.

### Codex Web MCP compatibility closure (2026-09-06)

The public `clawdbot.230385.xyz` Web instance was traced to the VPS Nginx route, `/opt/codex-app-server-web` on
`127.0.0.1:4173`, and its separate official Codex app-server on `127.0.0.1:43999`. Its initial `unknown`/
`inactive` MemoryBridge state was caused by Codex 0.151.0 rejecting `Authorization` emitted by the legacy
`http_headers_helper` as a reserved header. Because the MCP entry was optional, the service stayed nominally enabled
while its tools were absent from the live catalog.

The repository now uses `bearer_token_env_var`, adds an owner-only token-file wrapper for systemd-managed app-server
processes, documents the version boundary, and includes a wrapper regression test. The VPS configuration was
updated to the same bearer-token path, with an explicit `/usr/bin/codex` executable override for that host. Both
user services were reloaded and restarted successfully; no new reserved-header error appeared afterward.

The real public Web path then passed: authenticated login HTTP 200; official `mcpServerStatus/list` HTTP 200 with
`memorybridge`, `authStatus=bearerToken`, and 8 tools; an ephemeral `/tmp` thread start HTTP 200; and
`mcpServer/tool/call(memory_status)` HTTP 200 with `isError=false`. The Web write control was enabled for this tool
call while autonomous/unattended mode remained disabled. The probe was read-only and made no production collection
write. Local and VPS Hermes MCP tests both connected and discovered all 8 tools, and the Hermes plugin doctor passed.
The checked-in seal workflow now pins Codex 0.151.0 so this compatibility fix is covered by the next public CI run.

MCP URL knowledge alone still does not imply automatic capture: a future device must be issued a bearer token and
install its native adapter plus local spool daemon. A valid bearer token authorizes reads and writes, so per-device
tokens and rotation after exposure remain mandatory.

## What the Qdrant E2E actually destroys and recovers

The Qdrant matrix is not a mock and does not stop at a snapshot API success response. It exercises real Qdrant
containers and verifies:

1. durable raw writes and deterministic duplicate handling;
2. cursor/scan/recent reads;
3. asynchronous fallback-vector indexing;
4. vector retrieval;
5. embedding provider outage -> lexical fallback;
6. lexical miss -> raw/recent fallback;
7. snapshot creation;
8. immediate restore of the new snapshot into a disposable verification collection;
9. record semantic fingerprint from the restored copy;
10. portable JSONL.GZ generated from the restored copy rather than the live source;
11. deletion of the live raw collection;
12. restore from the archive only;
13. semantic verification after disaster restore;
14. refusal to overwrite an existing live collection unless `--force` is explicit.

The same E2E also starts the real packaged MCP server on Streamable HTTP, protects it with a bearer token, verifies
an unauthenticated request is rejected, sends a local-spool record through the real MCP `memory_put` wire path into
real Qdrant, and reads it back through MCP tools.

## Host-agent capture contracts

### Codex

The seal installs the real published `@openai/codex@0.151.0` package and exercises the current lifecycle payload
shapes used by MemoryBridge:

- `UserPromptSubmit` -> fsync user turn locally;
- `Stop` -> fsync assistant turn locally;
- `SessionEnd` -> queue a tiny persisted-transcript reference for reconciliation/backfill.

No network dependency is allowed in those hooks.

### OpenClaw

The seal installs both tested published OpenClaw versions into isolated state directories, installs the MemoryBridge
plugin through the real OpenClaw plugin CLI, enables it, runs the version-appropriate doctor/runtime inspection,
and then invokes real-shape capture hooks and verifies local fsynced records.

### Hermes

The seal installs the pinned upstream Hermes source editable, runs Hermes' own plugin doctor, writes a real Hermes
`SessionDB` session, then exercises MemoryBridge's `on_session_finalize` path and verifies the persisted messages
are exported into the local spool with stable metadata/idempotency.

## Failure injection covered

- client MCP/network failure preserves pending spool data;
- malformed spool data is quarantined without blocking later valid records;
- malformed transcript jobs are quarantined;
- embedding/New API outage does not block raw writes or model-free retrieval;
- vector failure degrades to lexical, then raw/recent;
- live Qdrant data is deliberately deleted and restored from a verified archive;
- unauthenticated MCP HTTP access is rejected;
- the production container starts the packaged server as a non-root user.

## Defects found by the seal process

The hardening process found real defects that ordinary unit tests had not exposed. They were fixed rather than
papered over:

- a Qdrant payload-only/vectorless write incompatibility exposed by real Qdrant;
- Codex capture relying too heavily on shutdown-only capture instead of per-turn local fsync + transcript reconciliation;
- an incorrect Hermes lifecycle/plugin packaging assumption (`plugin.py`/older hook shape) replaced by the current
  general-plugin package and durable `SessionDB` finalize path;
- OpenClaw version-specific plugin CLI/validation behavior exposed by real stable and beta packages;
- the server still importing the MCP v1 `FastMCP` path while the project declared MCP 2.x; the server was migrated
  to the MCP 2.x `MCPServer` API and the real HTTP wire path now tests it permanently;
- client systemd spool configuration previously relied on shell environment inheritance; the user unit now loads an
  optional persistent `~/.config/memorybridge.env` file;
- production-container startup had not been part of the seal gate; it now is.
- external raw Qdrant snapshots had no repository-level client-node verifier; the read-only `deployment-seal` gate now
  checks their pointer, sidecars, tar structure, schedule-time evidence and permission policy, with an isolated restore
  drill that cannot target an arbitrary collection name.
- local spool receipts and transcript references were created with the host umask; the spool now enforces owner-only
  directories/files at initialization and on every atomic write/move, with a regression test.

## Deliberate scope boundary

This seal does **not** claim that infrastructure unavailable to GitHub-hosted runners has been tested. In
particular, it does not certify the operator's private deployment of:

- uninterrupted future executions of the real New API endpoint and its `qwen3-embedding:0.6b` model;
- uninterrupted future CloudDrive2/FUSE backup executions and fsync semantics;
- the operator's production reverse proxy/TLS configuration;
- the operator's existing private Qdrant collections/data;
- machine-specific service permissions, storage capacity, firewall and DNS behavior.

Those are deployment-certification items, not missing repository features. The repository contains the same
recovery and wire paths needed to perform that final drill on the target server.

## Freeze rule after seal

No feature expansion is part of this seal. After `main` passes the same gates, changes should be limited to:

1. compatibility fixes required by upstream Codex/OpenClaw/Hermes/MCP/Qdrant changes;
2. data-integrity/recovery fixes;
3. security fixes;
4. tests or documentation necessary to prove those fixes.

A new product capability should require an explicit decision to unfreeze the feature surface rather than being
smuggled into maintenance.
