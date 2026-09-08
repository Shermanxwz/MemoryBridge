# ChatGPT deployment seal

Repository CI proves the server/tool/plugin contracts MemoryBridge controls. A real ChatGPT deployment is **SEALED** only after an operator completes every external gate below against the intended workspace.

## Gate A - server

- [ ] Public MCP URL is HTTPS and reaches only the reverse proxy/MCP service, not Qdrant.
- [ ] OAuth introspection mode is enabled and `MEMORYBRIDGE_BEARER_TOKENS` is empty for this deployment.
- [ ] Authorization issuer is the intended OAuth/OIDC provider.
- [ ] Required scope includes `memory`.
- [ ] Introspection credentials are stored only in the server's secret store/environment and are not in Git, Plugin files, shell history, or chat messages.
- [ ] Authorization server issues refresh tokens and advertises/supports `offline_access` (or the provider's documented equivalent) for ChatGPT connectivity.

## Gate B - ChatGPT custom app

- [ ] Developer mode was enabled by an authorized workspace role.
- [ ] App endpoint is the production `MEMORYBRIDGE_PUBLIC_MCP_URL`.
- [ ] OAuth completes successfully for a least-privilege test user.
- [ ] **Scan Tools** returns all eight MemoryBridge tools and no unexpected tools.
- [ ] Read-only actions are classified read-only; `memory_put` and `memory_ack` are state-changing/non-destructive.
- [ ] Workspace action controls and approvals were reviewed before publication.
- [ ] Draft app was published/approved for only the intended roles/groups.

## Gate C - real ChatGPT Chat

- [ ] `memory_status` succeeds through ChatGPT.
- [ ] `memory_search` returns a known disposable test record.
- [ ] `memory_put` writes a unique disposable marker after the expected confirmation/permission flow.
- [ ] A follow-up app invocation retrieves the marker.
- [ ] The operator verified that app selection is message-scoped and documented this for users.

## Gate D - ChatGPT Work / Plugin

- [ ] Generated Plugin `.app.json` contains the real workspace app id, not a placeholder.
- [ ] Generated Plugin does not contain `.mcp.json` or another direct MCP declaration.
- [ ] MemoryBridge skill is present and installed/available for the intended workspace role.
- [ ] The Plugin can retrieve durable context through the approved MemoryBridge app in a Work task.
- [ ] A durable Work outcome can be persisted through `memory_put` when write actions are permitted.

## Gate E - freeze

- [ ] Record the MemoryBridge Git commit, server image digest, OAuth provider configuration revision, ChatGPT app id, Plugin version, and seal date in the operator's deployment record.
- [ ] After changing tool names/schemas/annotations, refresh/review the ChatGPT app action snapshot before production use. Business workspaces may require recreate/republish under current product rules.
- [ ] Re-run this deployment seal after OAuth issuer/client changes, MCP endpoint changes, tool-contract changes, or Plugin app-binding changes.

The seal must fail rather than reinterpret an unavailable OpenAI host capability. In particular, the absence of a passive ChatGPT lifecycle hook is an explicit platform boundary, not a missing MemoryBridge test.
