# ChatGPT deployment seal

Repository CI proves the server/tool/plugin contracts MemoryBridge controls. A real ChatGPT deployment is **SEALED** only after an operator completes every external gate below against the intended workspace. A failed or unavailable gate is a deployment failure, not permission to reinterpret the product boundary.

## Gate A - server, OAuth and trust domain

- [ ] Public MCP URL is HTTPS and reaches only the reverse proxy/MCP service, not Qdrant.
- [ ] OAuth introspection mode is enabled and `MEMORYBRIDGE_BEARER_TOKENS` is empty for this deployment.
- [ ] `MEMORYBRIDGE_AUTH_ISSUER` is the intended OAuth/OIDC provider and is not a placeholder/default value.
- [ ] Required scope includes `memory`.
- [ ] Introspection credentials are stored only in the server's secret store/environment and are not in Git, Plugin files, shell history, or chat messages.
- [ ] A live active-token introspection response contains the configured issuer and binds the token to the exact `MEMORYBRIDGE_PUBLIC_MCP_URL` through `resource` or `aud`.
- [ ] Wrong-issuer, missing-issuer, trailing-slash-different issuer, wrong-resource, missing-resource, inactive, expired and insufficient-scope tokens fail closed.
- [ ] `MEMORYBRIDGE_OAUTH_ALLOWED_SUBJECTS` contains the exact OAuth/OIDC `sub` identity or intentionally trusted subject group authorized for this shared memory domain.
- [ ] A token with a missing or unlisted `sub` is rejected when the subject allowlist is enabled.
- [ ] If more than one subject is allowed, the operator explicitly accepts that those subjects share the same configured MemoryBridge collections; this deployment is not represented as row-level multi-tenant isolation.
- [ ] Mutually untrusted users/tenants use separate MemoryBridge instances/collection sets and credentials instead of one shared deployment.
- [ ] The authorization/OIDC provider's discovery metadata advertises the refresh/offline-access capability required by the selected ChatGPT OAuth flow.
- [ ] A least-privilege test identity receives a refresh token (or the provider's documented equivalent) and can reconnect after access-token renewal.

## Gate B - ChatGPT custom app

- [ ] Developer mode was enabled by an authorized workspace role.
- [ ] App endpoint is the production `MEMORYBRIDGE_PUBLIC_MCP_URL`.
- [ ] OAuth completes successfully for a least-privilege test user whose `sub` is in the configured trust-domain allowlist.
- [ ] **Scan Tools** returns all eight MemoryBridge tools and no unexpected tools.
- [ ] Read-only actions are classified read-only; `memory_put` and `memory_ack` are state-changing/non-destructive.
- [ ] For ordinary Chat/Work roles, sync-only `memory_scan`, `memory_since` and `memory_ack` are disabled through workspace action controls unless a documented synchronization use case explicitly needs them.
- [ ] Workspace app permissions, action controls, allowed roles/groups and approvals were reviewed before publication.
- [ ] Draft app was published/approved for only the intended roles/groups.
- [ ] The approved app's frozen tool/input snapshot matches the repository's eight-tool contract.

## Gate C - real ChatGPT Chat

- [ ] `memory_status` succeeds through ChatGPT.
- [ ] `memory_search` returns a known disposable test record.
- [ ] On a Business or Enterprise/Edu workspace with full MCP enabled, `memory_put` writes a unique disposable marker after the expected confirmation/permission flow.
- [ ] A follow-up app invocation retrieves the marker.
- [ ] The operator verified and documented that a selected app applies to the message where it is invoked; later requests that require fresh app data/actions invoke it again.
- [ ] The operator verified that retrieved memory is treated as untrusted context rather than a higher-priority instruction channel.
- [ ] The disposable write marker is removed from the operator-controlled test collection after evidence is recorded if zero-residue certification is required.

A read-only Pro developer-mode connection can prove the read path, but it cannot satisfy the full read/write deployment seal while OpenAI limits full MCP write/modify support to Business and Enterprise/Edu.

## Gate D - ChatGPT Chat / Work Plugin

- [ ] The generated Plugin `.app.json` contains the real underlying app id with one of the documented prefixes: `asdk_app_`, `connector_`, or `templated_apps_`.
- [ ] `.app.json` marks the MemoryBridge dependency with `"required": true`.
- [ ] If an admin copied a `plugin_asdk_app_...` technical id, the builder normalized it to the underlying `asdk_app_...` id.
- [ ] Generated Plugin does not contain `.mcp.json`, `mcp.json`, or another direct MCP declaration that would make an imported Plugin Desktop-only.
- [ ] Plugin manifest capabilities are the documented `Read` / `Write` values and its `apps` field points to `./.app.json`.
- [ ] GitHub marketplace import/sync succeeds with no processing errors.
- [ ] Workspace installation policy, authentication, required-app availability and action controls were configured in ChatGPT admin settings; no repository marketplace policy is treated as authoritative.
- [ ] MemoryBridge skill is present and installed/available for the intended workspace role.
- [ ] The Plugin can retrieve durable context through the approved MemoryBridge app in ChatGPT Chat and in a Work task.
- [ ] A durable Work outcome can be persisted through `memory_put` where full MCP write actions are permitted.

## Gate E - compatibility and freeze

- [ ] Record the MemoryBridge Git commit, server image digest, OAuth provider configuration revision, ChatGPT app id, Plugin version, workspace plan/surface, allowed-subject trust domain and seal date in the operator's deployment record.
- [ ] Record the exact eight scanned tool names plus the approved input/annotation snapshot used for acceptance.
- [ ] Record which sync-only actions are disabled/enabled for the production Chat/Work roles.
- [ ] After changing tool names/schemas/annotations, refresh and review the ChatGPT app actions before production use; do not assume server changes auto-propagate to an already approved app.
- [ ] Re-run this deployment seal after OAuth issuer/client/subject changes, MCP endpoint changes, tool-contract changes, Plugin app-binding changes, workspace permission changes, or an upstream ChatGPT/MCP capability change that affects this contract.

## Explicit platform and tenancy boundaries

ChatGPT custom apps are host-invoked MCP tools. They do not expose a passive per-turn lifecycle hook equivalent to Codex `UserPromptSubmit` / `Stop` / `SessionEnd`. Therefore a fully passed ChatGPT deployment seal certifies **official memory-enabled read/write workflows**, not guaranteed passive transcript capture.

MemoryBridge v0.2.0 also does not claim row-level tenant isolation inside one configured memory domain. OAuth subject allowlisting limits entry to the intended trust group; it does not partition records among allowed members. These boundaries must never be hidden by a test, Plugin skill, marketing statement, or seal report.
