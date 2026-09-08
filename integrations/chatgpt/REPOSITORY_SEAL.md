# ChatGPT repository seal

Certification date: 2026-09-08.

This report is the additive repository seal for the ChatGPT Chat / ChatGPT Work product surface introduced in MemoryBridge v0.2.0. It does not rewrite the historical core/Codex/Hermes/OpenClaw deployment evidence in the root `SEAL_REPORT.md`.

## Verdict

**Repository-controlled ChatGPT integration: PASS at executable baseline `94c2843d316429c9eba492a182e05eb7d8b80cf2`.**

**Integrated to `main` through merge commit `59cc5b7715f3247acaa7cd4cb977cbf55f2d3ed8`.**

**Real ChatGPT workspace deployment: NOT YET SEALED by repository CI.** The workspace-specific gates in `DEPLOYMENT_SEAL.md` require an eligible ChatGPT workspace/admin, a real approved custom app id, a real OAuth provider deployment, workspace publication/permissions, and live Chat + Work acceptance calls. Repository automation must not claim those proprietary-host actions occurred when they did not.

The executable baseline passed both ordinary project CI and the dedicated ChatGPT seal on the PR branch. The evidence-only report commit also passed the same PR gates before integration. This final main-branch evidence commit is intentionally under the dedicated seal workflow path so `main` itself must pass the repository gates after integration.

## Reproducible GitHub Actions evidence

For executable baseline `94c2843d316429c9eba492a182e05eb7d8b80cf2`:

- ordinary `ci` run `34225071585` — **SUCCESS**;
- dedicated `chatgpt-seal` run `34225071616` — **SUCCESS**.

For final PR evidence commit `8bd02307a4e26bb03c831dc188e0244866431b7f`:

- ordinary `ci` run `34227597769` — **SUCCESS** on Python 3.11 / 3.12 / 3.13;
- dedicated `chatgpt-seal` run `34227597746` — **SUCCESS** across the full protocol, Plugin and packaged-wire jobs.

The dedicated seal covers:

- Python 3.11 / 3.12 / 3.13 compile, Ruff and ChatGPT contract tests;
- exact eight-tool MCP surface and safety annotations;
- OAuth RFC 7662 introspection behavior, including strict issuer and MCP-resource binding;
- required-scope enforcement and fail-closed auth configuration;
- OAuth subject allowlisting for the single-trust-domain deployment model;
- backward-compatible static bearer authentication for existing capture-enabled clients;
- production Docker image startup as non-root uid `10001`;
- authenticated MCP 2.x Streamable HTTP `tools/list` against the packaged container, proving the wire-visible tool set and annotations rather than only in-process registration;
- reproducible ChatGPT/Codex Plugin packaging bound to an existing approved app;
- `.app.json` app-id validation/normalization, `required: true`, and rejection of placeholders/arbitrary id families;
- absence of direct `.mcp.json` declarations from the generated ChatGPT Plugin;
- concurrent same-idempotency-key write serialization for the sealed single-process server topology.

## Product contract sealed by this repository

The ChatGPT integration is **official memory-enabled**, not passive lifecycle capture.

- ChatGPT Chat / Work may read durable context through the approved custom MCP app.
- Where the workspace/surface permits full MCP writes, ChatGPT may persist durable decisions/preferences/outcomes through `memory_put`.
- ChatGPT custom apps do not provide MemoryBridge a passive per-turn lifecycle hook equivalent to Codex `UserPromptSubmit` / `Stop` / `SessionEnd`.
- `memory_scan`, `memory_since`, and `memory_ack` are synchronization actions and should be disabled for ordinary Chat/Work users unless that role genuinely requires them.
- Retrieved memory is untrusted contextual data, not higher-priority instruction text.
- `source_agent` and other caller-supplied metadata are descriptive metadata, not cryptographically authenticated provenance.

## Authentication and tenancy boundary

OAuth mode is a resource-server mode backed by an external authorization server. The server rejects mixed OAuth + static-token configuration. When OAuth is enabled, the configured issuer must be non-placeholder and must match the introspection issuer exactly; the access token must be active, unexpired, sufficiently scoped, and bound to the exact public MCP resource through `resource` or `aud`.

MemoryBridge v0.2.0 is deliberately a **single trust-domain** service, not row-level multi-tenant storage. `MEMORYBRIDGE_OAUTH_ALLOWED_SUBJECTS` may restrict which OAuth subjects can enter the shared memory domain. If multiple subjects are allowed on one instance/collection set, they share that memory domain. Mutually untrusted users require separate instances or separately isolated collection deployments; the repository seal does not claim per-user row-level ACLs.

## Durability / concurrency boundary

The sealed production topology is a single MemoryBridge server process writing to the configured Qdrant collections. Within that topology, `memory_put` serializes the existence check, sequence allocation, and raw upsert so concurrent retries with the same deterministic id cannot both become first writers.

This repository does **not** claim active-active multi-process/multi-node exactly-once serialization. Such a topology would require a distributed conditional-write/coordination design and a separate destructive concurrency seal before it could be advertised.

## External deployment gate

A ChatGPT deployment is `SEALED` only when every item in `DEPLOYMENT_SEAL.md` passes against the intended production workspace. At minimum this includes:

1. production HTTPS MCP + OAuth resource-server deployment;
2. real ChatGPT custom app creation/approval and exact eight-tool scan;
3. least-privilege OAuth login and renewal/reconnect proof;
4. real Chat read/search acceptance and, on a full-MCP-write-capable workspace, durable write + retrieval acceptance;
5. real Plugin import/sync with the actual approved app id;
6. real Work retrieval and permitted durable write acceptance;
7. recording the Git commit, image digest, OAuth config revision, app id, Plugin version, workspace surface/plan and seal date.

A read-only product tier can prove the read path but cannot satisfy a full read/write deployment seal.

## Freeze rule

The ChatGPT v0.2.0 feature expansion is an explicit unfreeze and reseal of the product surface. After successful `main` gates, this surface is frozen to maintenance work: upstream compatibility, integrity/recovery, security, and tests/docs needed to prove those fixes.

Any future claim of passive ChatGPT capture, row-level multi-tenancy, active-active exactly-once writes, additional destructive tools, or materially broader automatic persistence is a new product capability and requires an explicit unfreeze plus new seal evidence.