# Official ChatGPT integration

MemoryBridge supports ChatGPT through OpenAI's official remote MCP custom-app path. The same approved app can be referenced by a Plugin so the reusable MemoryBridge skill can participate in ChatGPT Chat and ChatGPT Work workflows.

This integration is intentionally described as **official memory-enabled**, not **capture-enabled**. OpenAI custom MCP apps expose host-invoked tools; they do not currently expose a passive per-turn lifecycle hook equivalent to Codex `UserPromptSubmit` / `Stop` / `SessionEnd`. MemoryBridge therefore never claims that registering the app gives it permission or ability to record every ChatGPT conversation.

Official references, re-verified 2026-09-08:

- Developer mode and MCP apps in ChatGPT: https://help.openai.com/en/articles/12584461
- Plugins in ChatGPT and Codex: https://help.openai.com/en/articles/20001256-plugins-in-chatgpt-and-codex
- Apps in ChatGPT: https://help.openai.com/en/articles/11487775
- Package your plugin: https://developers.openai.com/plugins/build/plugins
- Importing and syncing plugin marketplaces from GitHub: https://help.openai.com/en/articles/20001504
- Plugin management / existing-app references: https://learn.chatgpt.com/docs/enterprise/plugin-management
- MCP Python SDK authorization: https://github.com/modelcontextprotocol/python-sdk/tree/main/docs/server/auth

## What is sealed in this repository

The repository owns and tests the following host-facing contract:

- remote Streamable HTTP MCP endpoint;
- the exact eight MemoryBridge tools returned by `tools/list`;
- stable input schemas and human-readable descriptions;
- safety annotations that distinguish read-only tools from write/state tools;
- existing static bearer verification for capture-enabled agent clients;
- OAuth 2.1 resource-server mode using an external authorization server and RFC 7662 token introspection;
- strict issuer, scope and MCP-resource binding for OAuth-backed access;
- rejection of mixed static-token + OAuth-verifier configuration;
- a reproducible Plugin package generator and MemoryBridge skill;
- an official `.app.json` existing-app reference with `required: true`;
- validation/normalization of the app-id families OpenAI documents for `.app.json`;
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

The authorization server is a separate OAuth/OIDC service. MemoryBridge does not become an authorization server and does not store ChatGPT user passwords. For long-lived connectivity, configure the provider consistently with the current ChatGPT custom-app authentication requirements, including refresh/offline access where your provider and workspace flow require it.

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

The introspection response must be active, carry the configured issuer, and bind the access token to the exact public MCP resource through `resource` or `aud`. Missing or mismatched issuer/resource values are rejected. The introspection client secret belongs only on the MemoryBridge server. Do not commit it, paste it into ChatGPT, or place it in the generated Plugin package.

Existing Codex/Hermes/OpenClaw deployments may continue using `MEMORYBRIDGE_BEARER_TOKENS`. The server rejects configuration that enables both verifier modes at once so an OAuth deployment cannot silently fall back to a shared static token.

## Create the ChatGPT app

Current OpenAI setup is performed by a supported workspace admin/authorized developer in ChatGPT web:

1. Enable Developer mode for the eligible workspace/account.
2. Create/register a custom MCP app and provide the public HTTPS MemoryBridge MCP endpoint.
3. Select OAuth as the authentication mechanism and complete the authorization flow.
4. Scan/refresh tools. The app must expose exactly:
   - `memory_put`
   - `memory_scan`
   - `memory_since`
   - `memory_get`
   - `memory_recent`
   - `memory_ack`
   - `memory_status`
   - `memory_search`
5. Review the annotations/action risk. `memory_put` and `memory_ack` are state-changing but non-destructive; the remaining tools are read-only.
6. Run the deployment tests below, review the frozen tool snapshot/actions, then publish using the workspace's access/action controls.

Full MCP write/modify support is currently a beta capability for ChatGPT Business and Enterprise/Edu on ChatGPT web. Pro can use developer-mode custom apps with read/fetch permissions, but full MCP write is not currently available there. Do not advertise broader write availability without re-checking current OpenAI product documentation.

ChatGPT freezes an approved app's available tools and inputs. If MemoryBridge later changes a tool incompatibly, an admin must refresh/review the actions before relying on the new contract. This repository therefore treats the eight-tool surface as a compatibility contract.

## Package the Plugin for ChatGPT Chat / Work

After ChatGPT creates the registered MCP connection, obtain its technical/app id from the supported admin/developer surface. Current OpenAI documentation uses two related forms:

- a technical/plugin form such as `plugin_asdk_app_...` may appear in a ChatGPT URL or authoring flow;
- `.app.json` itself must reference the underlying app id, whose supported families are `asdk_app_...`, `connector_...`, or `templated_apps_...`.

The MemoryBridge builder accepts either the underlying supported app id or a copied `plugin_asdk_app_...` technical id and normalizes the latter to `asdk_app_...`. It rejects arbitrary/placeholder ids instead of generating a package that can only fail at import time.

Generate a plugin package:

```bash
python scripts/build_chatgpt_plugin.py \
  --app-id 'asdk_app_YOUR_REAL_ID' \
  --output ./dist/memorybridge-plugin
```

Or generate a GitHub-importable marketplace tree:

```bash
python scripts/build_chatgpt_plugin.py \
  --app-id 'asdk_app_YOUR_REAL_ID' \
  --marketplace-root ./dist/memorybridge-marketplace
```

The generated Plugin references the **existing approved ChatGPT app** through `.app.json`, marks it required, and deliberately contains no `.mcp.json`. Current OpenAI guidance states that imported plugins declaring MCP servers directly can be marked Desktop only; referencing the already-approved app preserves the normal ChatGPT app permission/authentication path.

The marketplace generator includes repository policy hints for local authoring compatibility, but GitHub marketplace import does **not** apply repository installation/authentication policies to the workspace. Configure Available/Installed, authentication, required-app access and action controls in ChatGPT workspace settings after import.

The bundled source skill is `integrations/chatgpt/skill/memorybridge/SKILL.md`; the generated package places it at `skills/memorybridge/SKILL.md`. It tells ChatGPT to retrieve durable context when relevant and to write only durable decisions/preferences/outcomes, never to behave as a passive transcript recorder.

## Functional acceptance prompts

Run these against the draft app before publication:

- Read-only: `@MemoryBridge check MemoryBridge status.` -> `memory_status`
- Retrieval: `@MemoryBridge search durable memory for <known unique test phrase>.` -> `memory_search`
- Write (where full MCP is enabled): `@MemoryBridge remember this durable test marker: <unique value>.` -> `memory_put` (confirm if ChatGPT asks)
- Verify: `@MemoryBridge find the exact durable test marker I just saved.` -> `memory_search`/`memory_get`

Use a disposable marker and delete it directly from the operator-controlled test collection if your production policy requires a zero-residue certification. MemoryBridge intentionally does not expose a general `memory_delete` MCP tool.

## Operational truth table

| Surface | Official MCP read | Official MCP write | Passive lifecycle capture |
|---|---:|---:|---:|
| ChatGPT Chat | Yes, where custom apps are supported | Business/Enterprise/Edu full-MCP beta, subject to controls | No |
| ChatGPT Work via Plugin + approved app | Yes, subject to plugin/app availability | Subject to full-MCP availability and app action controls | No |
| Codex | Yes | Yes | Yes, with MemoryBridge native hooks |
| Hermes | Yes | Yes | Yes, with MemoryBridge plugin |
| OpenClaw | Yes | Yes | Yes, with MemoryBridge plugin |

This distinction is part of the product contract and must not be weakened in marketing or seal reports.
