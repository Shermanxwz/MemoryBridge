# MemoryBridge Seal Report

Seal scope: repository behavior that can be reproduced on public CI infrastructure.

Executable/configuration baseline certified before this report was added:

`6028079bd17a4d4f2d1db026b4bef6af0b70f38a`

Certification date: 2026-08-29.

## Verdict

**Repository seal candidate: PASS.**

The baseline above passed both the ordinary CI matrix and the destructive/contract `seal` workflow. This report is
documentation-only; after it is committed, the same workflows must pass again on the final branch commit and then
again after fast-forwarding `main` before the repository is called sealed.

## Reproducible evidence

### CI

GitHub Actions run `33242349401` completed successfully for the baseline above.

Matrix:

- Python 3.11: compile, tests, Ruff — PASS
- Python 3.12: compile, tests, Ruff — PASS
- Python 3.13: compile, tests, Ruff — PASS
- MCP server construction is part of the normal test suite so an SDK/server API mismatch cannot silently survive CI.

### Seal workflow

GitHub Actions run `33242349420` completed successfully for the same baseline.

Certified jobs:

- Production Docker image build and packaged MCP server startup as uid `10001` — PASS
- Qdrant `v1.18.3` destructive E2E — PASS
- Qdrant `v1.19.0` destructive E2E — PASS
- Codex `0.150.1` native capture contract — PASS
- OpenClaw `2026.7.1-2` stable plugin install/load/capture contract — PASS
- OpenClaw `2026.8.1-beta.3` plugin install/load/capture contract — PASS
- Hermes upstream commit `ac6c8028e00d01ee2f299ba7fd03329c7f10382d` plugin doctor + SessionDB capture contract — PASS

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

The seal installs the real published `@openai/codex@0.150.1` package and exercises the current lifecycle payload
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

## Deliberate scope boundary

This seal does **not** claim that infrastructure unavailable to GitHub-hosted runners has been tested. In
particular, it does not certify the operator's private deployment of:

- the real New API endpoint and its `qwen3-embedding:0.6b` model;
- the real CloudDrive2/FUSE mount and its permissions/fsync semantics;
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
