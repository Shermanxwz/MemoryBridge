# Codex integration

1. Install MemoryBridge in the same user environment as Codex.
2. Merge `hooks.json.example` into `~/.codex/hooks.json` (do not overwrite unrelated hooks).
3. Configure the remote MCP server with Codex:

```bash
codex mcp add memorybridge \
  --url https://memory.example.com/mcp \
  --bearer-token-env-var MEMORYBRIDGE_MCP_TOKEN
codex mcp list
```

For Codex 0.151.0 and newer, use `bearer_token_env_var`. Do not use a header helper to emit `Authorization`: the
current Codex app server treats that helper result as a reserved header and drops the MCP server from the live tool
catalog. The `codex mcp add` command writes the correct configuration:

```toml
[mcp_servers.memorybridge]
url = "https://memory.example.com/mcp"
bearer_token_env_var = "MEMORYBRIDGE_MCP_TOKEN"
required = false
```

The environment variable must contain the token value, not the token-file path. For a systemd-managed Codex Web app
server, install `scripts/memorybridge_codex_app_server.sh` as `/usr/local/libexec/memorybridge-codex-app-server` and
install `deploy/systemd/codex-official-app-server-memorybridge.conf` as a drop-in for the active app-server unit.
The wrapper reads the owner-only `~/.config/memorybridge.token` and exports it only to the Codex process. Reload the
unit after installing the drop-in. For a one-shot CLI check, use an ephemeral environment value:

```bash
MEMORYBRIDGE_MCP_TOKEN="$(tr -d '\r\n' < ~/.config/memorybridge.token)" codex mcp list
```

The wrapper resolves `codex` from the service `PATH`; set `MEMORYBRIDGE_CODEX_BIN` in the drop-in if the executable
is installed elsewhere.

`codex mcp list` verifies the registration; the Web app-server smoke test should call its official
`mcpServerStatus/list` method and confirm that `memorybridge` exposes tools.

The former `scripts/memorybridge_codex_headers.py` remains only for older Codex clients that accept an Authorization
header from a helper. The MCP URL is not a secret, but the bearer token is. A URL-only registration does not
authenticate and does not enable automatic capture.

MemoryBridge uses three native Codex lifecycle hooks, and none of them performs network I/O:

- `UserPromptSubmit` fsyncs the submitted user turn into the local spool.
- `Stop` fsyncs the completed assistant turn into the local spool.
- `SessionEnd` fsyncs only a tiny reference to Codex's persisted transcript. The background daemon later reads
  that transcript as an idempotent reconciliation/backfill source.

This closes the normal shutdown dependency: MCP, Qdrant, or the network may all be unavailable and Codex still
finishes normally with its current turns preserved locally. Run `memorybridge spool-sync --daemon` as a user
service so pending records are delivered automatically when connectivity returns.

Verify the capture half and the delivery half independently: invoke the hook with a synthetic payload in an isolated
spool during installation, then use `memorybridge status` to confirm the authenticated server is reachable. The
server worker indexes raw records asynchronously; capture remains successful while indexing is pending.

For retrieval, use Codex native Memories/index first when available. `memory_search` is the server-side fallback.
