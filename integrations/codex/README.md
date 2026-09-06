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

For a persistent, shell-independent setup, use the checked-in header helper in `scripts/memorybridge_codex_headers.py`
and configure:

```toml
[mcp_servers.memorybridge]
url = "https://memory.example.com/mcp"
http_headers_helper = "python3 /absolute/path/to/MemoryBridge/scripts/memorybridge_codex_headers.py"
required = false
```

The helper reads `~/.config/memorybridge.token`; keep that file owner-only (`0600`). The MCP URL is not a secret, but
the bearer token is. A URL-only registration does not authenticate and does not enable automatic capture.

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
