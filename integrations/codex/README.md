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

`SessionEnd` only fsyncs a tiny reference to Codex's already-flushed transcript. It does not read the full
transcript or perform network I/O inside Codex's short shutdown-hook budget. The background spool daemon reads,
chunks and delivers it later. Run `memorybridge spool-sync --daemon` as a user service.

For retrieval, use Codex native Memories/index first when available. `memory_search` is the fallback tool.
