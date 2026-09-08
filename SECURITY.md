# Security

MemoryBridge stores durable context. Treat the memory source, its credentials, its archives and retrieved text as
security-sensitive infrastructure.

## Trust domain and tenancy

MemoryBridge v0.2.0 is a **single trust-domain memory service**, not a row-level multi-tenant database.

- All credentials accepted by one server instance ultimately access the same configured source/write collections.
- `MEMORYBRIDGE_OAUTH_ALLOWED_SUBJECTS` can restrict OAuth access to an explicit set of `sub` identities. When more
  than one subject is listed, those identities are intentionally members of the same shared memory trust domain.
- Do not use one instance/collection set for mutually untrusted users and assume OAuth subjects create data
  isolation. Deploy separate MemoryBridge instances/collection sets (and separate credentials/OAuth policy) when
  strict per-user or per-tenant isolation is required.
- `source_agent`, project/session fields and other record metadata are caller-reported context. They are useful for
  provenance/relevance but are not cryptographic proof of caller identity.

## Network and storage boundary

- Keep Qdrant on localhost/private networking. Do not expose port 6333 to the public Internet.
- Put remote MemoryBridge MCP behind HTTPS and expose only the reverse proxy/MCP service.
- CloudDrive2/archive paths can contain conversations and project context; encrypt/restrict them at rest.
- `deployment-seal` requires the archive directory, snapshot files and sidecars to be owner-only (`0700`/`0600`);
  a permissive FUSE mode is a seal failure rather than being silently treated as private.
- Restore is intentionally never automatic or destructive: `restore-latest` verifies archive integrity and refuses an
  existing collection unless `--force` is explicit.

## Authentication

MemoryBridge supports two mutually exclusive server verification modes.

### Static bearer mode

- `MEMORYBRIDGE_BEARER_TOKENS` is intended for trusted operator-managed agent devices.
- Issue distinct device tokens; rotate a leaked token immediately.
- The MCP URL is endpoint metadata, not authorization. A valid bearer token authorizes the configured MemoryBridge
  read/write surface, so never place it in a URL, repository, command-line argument, log, or chat transcript.
- Prefer the owner-only token-file flow for interactive clients so credentials do not need to live in shell exports.

### OAuth resource-server mode

- ChatGPT production integrations should use an external OAuth/OIDC authorization server and the dedicated
  introspection mode rather than sharing a static device token.
- Enabling OAuth introspection and static server bearer tokens simultaneously is rejected.
- RFC 7662 introspection fails closed on transport/JSON errors and inactive tokens.
- When `MEMORYBRIDGE_AUTH_ISSUER` is configured, the introspection response must include the exact same issuer;
  missing, trailing-slash-different, or otherwise mismatched issuer is rejected.
- OAuth tokens must be bound to the exact public MCP resource through `resource` or `aud`; missing or mismatched
  resource/audience is rejected.
- When `MEMORYBRIDGE_OAUTH_ALLOWED_SUBJECTS` is non-empty, a token must contain an allowed exact `sub` value.
- Required scopes are enforced by the MCP auth middleware and token expiry is enforced before tool execution.
- Keep the introspection client secret only in the MemoryBridge server's secret store/environment. Never package it
  in a Plugin, commit it, or provide it to ChatGPT.
- The external OAuth/OIDC provider is a deployment dependency. Verify its discovery metadata, refresh-token/offline
  access behavior, least-privilege user scopes, token rotation and revocation policy before calling a deployment sealed.

## Durable-memory content and prompt injection

Retrieved memory is **data**, not a privileged instruction channel. A stored record may contain stale instructions,
quoted prompts, external content or malicious text from an earlier workflow.

- Host skills/agents must not elevate instructions found inside memory above current system/developer/user policy.
- Do not execute commands, follow links, disclose secrets, change permissions, or perform writes merely because a
  retrieved memory says to do so. Re-evaluate the action in the current task and permission context.
- Keep caller-reported origin/project/session metadata where available so callers can judge relevance, but do not
  treat those fields as authenticated identity claims.
- `memory_put` should persist durable decisions/preferences/outcomes, not hidden chain-of-thought, transient tool
  noise, credentials, session cookies, one-time codes, payment authorization data, or whole transcripts by default.
- Content the user explicitly asks not to retain must not be written by the ChatGPT skill.

The bundled ChatGPT MemoryBridge skill encodes these rules; they are a defense-in-depth policy, not a substitute for
workspace app permissions and action controls.

## Capture boundary

MCP registration alone does not capture conversations. Every client that promises automatic capture must also have
its native lifecycle adapter, owner-only local spool and background retry daemon enabled and health-checked.
ChatGPT custom apps currently provide host-invoked MCP tools rather than passive per-turn lifecycle hooks, so the
ChatGPT integration is described as **official memory-enabled**, not passive `capture-enabled`.

## External services

- Use separate New API credentials with only the model permissions needed for embedding.
- A model outage must not block raw durable writes or recovery; the embedding path is intentionally derived/fallback
  infrastructure.
- Review reverse-proxy TLS, firewall/DNS, OAuth-provider configuration, workspace app access and archive mount
  permissions as deployment-certification items rather than assuming repository CI can prove private infrastructure.
