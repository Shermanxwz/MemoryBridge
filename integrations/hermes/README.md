# Hermes integration

Install MemoryBridge in the same Python environment as Hermes, then copy this directory to an enabled Hermes
general-plugin location (normally `~/.hermes/plugins/memorybridge/`). Keep both `plugin.py` and `plugin.yaml`.

The plugin registers Hermes' native `on_session_finalize` lifecycle hook. Hermes has already persisted the full
conversation in its own `SessionDB`/`~/.hermes/state.db` by that point; MemoryBridge reads those durable messages
and fsyncs them into its local spool with deterministic idempotency keys. The hook never performs MCP/Qdrant or
other network I/O.

Validate an installation with Hermes itself:

```bash
hermes plugins doctor ~/.hermes/plugins/memorybridge --ci
```

Add the remote server to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  memorybridge:
    url: https://memory.example.com/mcp
    headers:
      Authorization: "Bearer YOUR_DEVICE_TOKEN"
    enabled: true
```

Hermes discovers the MCP tools at startup. Native memory/index remains preferred. If native retrieval is absent,
MemoryBridge `memory_search` degrades automatically from vector -> lexical -> raw/recent. Capture and retrieval
remain independent, so a remote outage never blocks Hermes from finishing a conversation.
