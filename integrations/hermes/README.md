# Hermes integration

Copy `plugin.py` to an enabled Hermes general-plugin directory (normally
`~/.hermes/plugins/memorybridge/`) after installing the `memorybridge` Python package in the same environment.
The plugin uses `post_llm_call` and only fsyncs to the local spool.

Add the server to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  memorybridge:
    url: https://memory.example.com/mcp
    headers:
      Authorization: "Bearer YOUR_DEVICE_TOKEN"
    enabled: true
```

Hermes discovers the MCP tools at startup. Native memory/index remains preferred; MemoryBridge's
`memory_search` automatically degrades vector -> lexical -> raw/recent when native retrieval is unavailable.
