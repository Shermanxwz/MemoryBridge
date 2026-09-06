# Hermes integration

Install MemoryBridge in the same Python environment as Hermes, then copy this directory to an enabled Hermes
general-plugin location (normally `~/.hermes/plugins/memorybridge/`). Keep `__init__.py` and `plugin.yaml` together.

```bash
mkdir -p ~/.hermes/plugins/memorybridge
cp integrations/hermes/__init__.py integrations/hermes/plugin.yaml integrations/hermes/README.md \
  ~/.hermes/plugins/memorybridge/
hermes plugins enable memorybridge
hermes plugins doctor ~/.hermes/plugins/memorybridge --ci
```

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

Store `MCP_MEMORYBRIDGE_API_KEY=<device-token>` in `~/.hermes/.env`, set that file to mode `0600`, and validate the
wire connection:

```bash
hermes mcp test memorybridge
```

The MCP entry and the native plugin are separate. The entry enables tool access; the plugin reads Hermes' persisted
`SessionDB` at `on_session_finalize` and fsyncs records into the local spool. A background `memorybridge spool-sync`
daemon must be running for those records to reach the server. The hook never performs network I/O.

Hermes discovers the MCP tools at startup. Native memory/index remains preferred. If native retrieval is absent,
MemoryBridge `memory_search` degrades automatically from vector -> lexical -> raw/recent. Capture and retrieval
remain independent, so a remote outage never blocks Hermes from finishing a conversation.
