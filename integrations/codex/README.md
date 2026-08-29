# Codex integration

1. Install MemoryBridge in the same user environment as Codex.
2. Merge `hooks.json.example` into `~/.codex/hooks.json` (do not overwrite unrelated hooks).
3. Configure the remote MCP server in `~/.codex/config.toml`:

```toml
[mcp_servers.memorybridge]
url = "https://memory.example.com/mcp"
bearer_token_env_var = "MEMORYBRIDGE_MCP_TOKEN"
required = false
```

MemoryBridge uses three native Codex lifecycle hooks, and none of them performs network I/O:

- `UserPromptSubmit` fsyncs the submitted user turn into the local spool.
- `Stop` fsyncs the completed assistant turn into the local spool.
- `SessionEnd` fsyncs only a tiny reference to Codex's persisted transcript. The background daemon later reads
  that transcript as an idempotent reconciliation/backfill source.

This closes the normal shutdown dependency: MCP, Qdrant, or the network may all be unavailable and Codex still
finishes normally with its current turns preserved locally. Run `memorybridge spool-sync --daemon` as a user
service so pending records are delivered automatically when connectivity returns.

For retrieval, use Codex native Memories/index first when available. `memory_search` is the server-side fallback.
