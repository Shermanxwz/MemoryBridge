"""Seal the MCP surface ChatGPT scans when a custom app is created.

This is intentionally a protocol contract, not a claim that CI can automate the
proprietary ChatGPT web host. The host-facing facts we own are the tool names,
schemas, and safety annotations returned by MCP tools/list.
"""

from __future__ import annotations

import asyncio

from memorybridge.config import Settings
from memorybridge.server import build_server

EXPECTED = {
    "memory_put",
    "memory_scan",
    "memory_since",
    "memory_get",
    "memory_recent",
    "memory_ack",
    "memory_status",
    "memory_search",
}
READ_ONLY = EXPECTED - {"memory_put", "memory_ack"}


async def main() -> None:
    server = build_server(Settings())
    try:
        tools = await server.list_tools()
        by_name = {tool.name: tool for tool in tools}
        assert set(by_name) == EXPECTED
        for name, tool in by_name.items():
            assert tool.description
            assert tool.input_schema.get("type") == "object"
            assert tool.annotations is not None
            if name in READ_ONLY:
                assert tool.annotations.read_only_hint is True
                assert tool.annotations.idempotent_hint is True
            else:
                assert tool.annotations.read_only_hint is False
                assert tool.annotations.destructive_hint is False
            assert tool.annotations.open_world_hint is False
        print("ChatGPT custom MCP Scan Tools contract: PASS")
    finally:
        await server._memorybridge_service.close()


if __name__ == "__main__":
    asyncio.run(main())
