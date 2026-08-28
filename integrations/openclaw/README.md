# OpenClaw integration

Install/copy this native plugin as `memorybridge`, enable it, and restart the active Gateway. The plugin uses
OpenClaw's typed `message_received` and `agent_end` hooks and directly fsyncs the local spool; it never performs
network I/O or launches a helper process in the hook path.

Register the remote MCP server with OpenClaw (static bearer example):

```bash
openclaw mcp set memorybridge '{"url":"https://memory.example.com/mcp","transport":"streamable-http","headers":{"Authorization":"Bearer YOUR_DEVICE_TOKEN"}}'
openclaw mcp probe memorybridge --json
```

The capture path and MCP retrieval path are independent: a remote outage does not block chat, because capture
lands locally first and the spool daemon retries later.
