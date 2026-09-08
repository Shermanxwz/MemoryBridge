# Official ChatGPT integration

MemoryBridge supports ChatGPT through OpenAI's official remote MCP app path. The same approved app can be referenced by a Plugin so the reusable MemoryBridge skill can participate in ChatGPT Chat and ChatGPT Work workflows.

This integration is intentionally described as **official memory-enabled**, not **capture-enabled**. OpenAI custom MCP apps expose host-invoked tools; they do not currently expose a passive per-turn lifecycle hook equivalent to Codex `UserPromptSubmit` / `Stop` / `SessionEnd`. App selection in ChatGPT applies to the message where the app is used, and a later message that needs new data or another action must invoke the app again.

Official references, verified 2026-09-08:

- Developer mode and MCP apps in ChatGPT: https://help.openai.com/en/articles/12584461
- Plugins in ChatGPT and Codex: https://help.openai.com/en/articles/20001256-plugins-in-chatgpt-and-codex
- Apps in ChatGPT: https://help.openai.com/en/articles/11487775
- Importing and syncing plugin marketplaces from GitHub: https://help.openai.com/en/articles/20001504
- MCP Python SDK authorization: https://github.com/modelcontextprotocol/python-sdk/tree/main/docs/server/auth

## What is sealed in this repository

The repository owns and tests the following host-facing contract:

- remote Streamable HTTP MCP endpoint;
- the exact eight MemoryBridge tools returned by `tools/list`;
- stable input schemas and human-readable descriptions;
- safety annotations that distinguish read-only tools from write/state tools;
- existing static bearer verification for capture-enabled agent clients;
- OAuth 2.1 resource-server mode using an external authorization server and RFC 7662 token introspection;
- rejection of mixed static-token + OAuth-verifier configuration;
- a reproducible Plugin package generator and MemoryBridge skill;
- CI contracts that exercise all of the above on supported Python versions.

What CI cannot honestly seal is OpenAI's proprietary ChatGPT web host, workspace approval, an administrator's OAuth provider configuration, or the workspace-specific app id generated when an admin creates the custom app. Those are deployment gates and are covered by `DEPLOYMENT_SEAL.md`.

## Production topology

```text
ChatGPT Chat / Work
        |
        | approved custom app / Plugin
        | OAuth 2.1 access token
        v
https://memory.example.com/mcp
        |
        | MemoryBridge MCP resource server
        | RFC 7662 token introspection
        v
Authorization Server          Qdrant
        |                         ^
        +---- user auth           |
                                  |
MemoryBridge MCP ---------------+
```

The authorization server is a separate OAuth/OIDC service. MemoryBridge does not become an authorization server and does not store ChatGPT user passwords. For long-lived connectivity, configure the provider to issue refresh tokens and advertise/support `offline_access`, as required by the current ChatGPT custom-app guidance.

## Server configuration

For an OAuth-backed ChatGPT deployment, leave `MEMORYBRIDGE_BEARER_TOKENS` empty and configure:

```env
MEMORYBRIDGE_PUBLIC_MCP_URL=https://memory.example.com/mcp
MEMORYBRIDGE_AUTH_ISSUER=https://auth.example.com
MEMORYBRIDGE_AUTH_REQUIRED_SCOPES=memory
MEMORYBRIDGE_OAUTH_INTROSPECTION_URL=https://auth.example.com/oauth2/introspect
MEMORYBRIDGE_OAUTH_INTROSPECTION_CLIENT_ID=memorybridge-resource-server
MEMORYBRIDGE_OAUTH_INTROSPECTION_CLIENT_SECRET=...
```

The introspection client secret belongs only on the MemoryBridge server. Do not commit it, paste it into ChatGPT, or place it in the generated Plugin package.

Existing Codex/Hermes/OpenClaw deployments may continue using `MEMORYBRIDGE_BEARER_TOKENS`. The server rejects configuration that enables both verifier modes at once so an OAuth deployment cannot silently fall back to a shared static token.

## Create the ChatGPT app

Current OpenAI setup is performed by a supported workspace admin/authorized developer in ChatGPT web:

1. Enable Developer mode for the eligible workspace/account.
2. Go to Apps -> Create and provide the public HTTPS MemoryBridge MCP endpoint.
3. Select OAuth as the authentication mechanism and complete the authorization flow.
4. Choose **Scan Tools**. The scan must return exactly:
   - `memory_put`
   - `memory_scan`
   - `memory_since`
   - `memory_get`
   - `memory_recent`
   - `memory_ack`
   - `memory_status`
   - `memory_search`
5. Review the annotations/action risk. `memory_put` and `memory_ack` are state-changing but non-destructive; the remaining tools are read-only.
6. Create the draft, run the deployment tests below, then publish it using the workspace's action/access controls.

Custom full-MCP write/modify support is currently beta and depends on ChatGPT plan, workspace, role, and surface. OpenAI's current help center documents full MCP for Business and Enterprise/Edu on ChatGPT web; do not advertise broader availability without re-checking the current product documentation.

## Package the Plugin for ChatGPT Work

After ChatGPT creates the workspace-specific app, copy the app id from the workspace admin surface. The id is not a password, but it is workspace-specific and must not be invented or copied from another workspace.

Generate a plugin package:

```bash
python scripts/build_chatgpt_plugin.py \
  --app-id 'YOUR_REAL_CHATGPT_APP_ID' \
  --output ./dist/memorybridge-plugin
```

Or generate a GitHub-importable marketplace tree:

```bash
python scripts/build_chatgpt_plugin.py \
  --app-id 'YOUR_REAL_CHATGPT_APP_ID' \
  --marketplace-root ./dist/memorybridge-marketplace
```

The generated Plugin references the **existing ChatGPT app** through `.app.json` and deliberately contains no `.mcp.json`. Current OpenAI guidance warns that imported plugins declaring their own MCP servers can be marked Desktop only; referencing the already-approved app preserves the official ChatGPT app permission/authentication path.

The bundled skill is `skill/memorybridge/SKILL.md`. It tells ChatGPT to retrieve durable context when relevant and to write only durable decisions/preferences/outcomes, never to behave as a passive transcript recorder.

## Functional acceptance prompts

Run these against the draft app before publication:

- Read-only: `@MemoryBridge check MemoryBridge status.` -> `memory_status`
- Retrieval: `@MemoryBridge search durable memory for <known unique test phrase>.` -> `memory_search`
- Write: `@MemoryBridge remember this durable test marker: <unique value>.` -> `memory_put` (confirm if ChatGPT asks)
- Verify: `@MemoryBridge find the exact durable test marker I just saved.` -> `memory_search`/`memory_get`

Use a disposable marker and delete it directly from the operator-controlled test collection if your production policy requires a zero-residue certification. MemoryBridge intentionally does not expose a general `memory_delete` MCP tool.

## Operational truth table

| Surface | Official MCP read | Official MCP write | Passive lifecycle capture |
|---|---:|---:|---:|
| ChatGPT Chat | Yes, where custom apps are supported | Yes, where full MCP/write is supported and allowed | No |
| ChatGPT Work via Plugin + approved app | Yes, subject to app/plugin availability | Yes, subject to app action controls | No |
| Codex | Yes | Yes | Yes, with MemoryBridge native hooks |
| Hermes | Yes | Yes | Yes, with MemoryBridge plugin |
| OpenClaw | Yes | Yes | Yes, with MemoryBridge plugin |

This distinction is part of the product contract and must not be weakened in marketing or seal reports.
