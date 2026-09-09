import asyncio
from unittest.mock import AsyncMock

from memorybridge.chatgpt_ui import ARCHIVE_RESOURCE_URI, ARCHIVE_WIDGET_HTML
from memorybridge.config import Settings
from memorybridge.server import build_server


def test_chatgpt_archive_ui_is_opt_in_and_does_not_expand_the_legacy_surface():
    legacy = build_server(Settings(chatgpt_ui_enabled=False))
    enabled = build_server(Settings(chatgpt_ui_enabled=True))
    try:
        legacy_tools = {tool.name for tool in asyncio.run(legacy.list_tools())}
        enabled_tools = {tool.name for tool in asyncio.run(enabled.list_tools())}
        assert "memorybridge_archive_panel" not in legacy_tools
        assert "memorybridge_archive_save" not in legacy_tools
        assert {"memorybridge_archive_panel", "memorybridge_archive_save"} <= enabled_tools
        assert enabled_tools - legacy_tools == {"memorybridge_archive_panel", "memorybridge_archive_save"}
    finally:
        asyncio.run(legacy._memorybridge_service.close())
        asyncio.run(enabled._memorybridge_service.close())


def test_chatgpt_archive_widget_uses_the_mcp_apps_bridge_and_safe_button_text():
    assert ARCHIVE_RESOURCE_URI.startswith("ui://")
    assert ARCHIVE_RESOURCE_URI.endswith("archive-v4.html")
    assert "确认归档" in ARCHIVE_WIDGET_HTML
    assert 'request("tools/call"' in ARCHIVE_WIDGET_HTML
    assert "memorybridge_archive_save" in ARCHIVE_WIDGET_HTML
    assert "bearer token" in ARCHIVE_WIDGET_HTML.lower()
    assert "ui/message" not in ARCHIVE_WIDGET_HTML
    assert "sendFollowUpMessage" not in ARCHIVE_WIDGET_HTML


def test_chatgpt_archive_tools_link_to_the_ui_resource():
    server = build_server(Settings(chatgpt_ui_enabled=True))
    try:
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        panel = tools["memorybridge_archive_panel"].model_dump(by_alias=True)
        assert panel["_meta"]["ui"]["resourceUri"] == ARCHIVE_RESOURCE_URI
        assert panel["_meta"]["openai/outputTemplate"] == ARCHIVE_RESOURCE_URI
        save = tools["memorybridge_archive_save"].model_dump(by_alias=True)
        assert save["_meta"]["ui"]["visibility"] == ["model", "app"]
        assert save["_meta"]["openai/widgetAccessible"] is True
    finally:
        asyncio.run(server._memorybridge_service.close())


def test_chatgpt_archive_panel_is_ui_only_and_save_writes_chatgpt_metadata():
    server = build_server(Settings(chatgpt_ui_enabled=True))
    try:
        service = server._memorybridge_service
        service.put = AsyncMock(
            return_value={"stored": True, "id": "archive-id", "seq": 7, "index_status": "pending"}
        )

        panel = asyncio.run(
            server.call_tool(
                "memorybridge_archive_panel",
                {
                    "summary": "已确定使用 ChatGPT 应用内归档卡片。",
                    "title": "MemoryBridge ChatGPT 归档",
                    "decisions": ["只启用 ChatGPT 专用入口"],
                    "next_steps": ["在 ChatGPT 中刷新工具并测试"],
                    "project": "MemoryBridge",
                    "idempotency_key": "archive-test-1",
                },
            )
        )
        assert panel.structured_content["state"] == "draft"
        assert panel.structured_content["summary"] == "已确定使用 ChatGPT 应用内归档卡片。"
        service.put.assert_not_awaited()

        result = asyncio.run(
            server.call_tool(
                "memorybridge_archive_save",
                {
                    "summary": "已确定使用 ChatGPT 应用内归档卡片。",
                    "title": "MemoryBridge ChatGPT 归档",
                    "decisions": ["只启用 ChatGPT 专用入口"],
                    "next_steps": ["在 ChatGPT 中刷新工具并测试"],
                    "project": "MemoryBridge",
                },
            )
        )
        assert result.structured_content["stored"] is True
        put = service.put.await_args.args[0]
        assert put.source_agent == "chatgpt"
        assert put.source_device == "chatgpt-app"
        assert put.metadata["archive_trigger"] == "chatgpt_archive_card"
        assert "密码" not in put.content

        namespaced_result = asyncio.run(
            server.call_tool(
                "memorybridge_mcp_archive.memorybridge_archive_save",
                {"summary": "组件调用名称兼容已验证。"},
            )
        )
        assert namespaced_result.structured_content["stored"] is True

        escaped_namespaced_result = asyncio.run(
            server.call_tool(
                r"memorybridge\_mcp\_archive.memorybridge\_archive\_save",
                {"summary": "转义组件调用名称兼容已验证。"},
            )
        )
        assert escaped_namespaced_result.structured_content["stored"] is True
    finally:
        asyncio.run(server._memorybridge_service.close())


def test_chatgpt_archive_panel_without_a_draft_never_writes_or_requests_a_prompt():
    server = build_server(Settings(chatgpt_ui_enabled=True))
    try:
        service = server._memorybridge_service
        service.put = AsyncMock()
        result = asyncio.run(server.call_tool("memorybridge_archive_panel", {}))
        assert result.structured_content["state"] == "draft_required"
        assert result.structured_content["stored"] is not True
        service.put.assert_not_awaited()
    finally:
        asyncio.run(server._memorybridge_service.close())


def test_chatgpt_archive_save_rejects_labeled_credentials_before_qdrant_write():
    server = build_server(Settings(chatgpt_ui_enabled=True))
    try:
        service = server._memorybridge_service
        service.put = AsyncMock()
        result = asyncio.run(
            server.call_tool(
                "memorybridge_archive_save",
                {"summary": "部署密码: do-not-store-this-value"},
            )
        )
        assert result.structured_content["stored"] is False
        assert "凭据" in result.structured_content["error"]
        service.put.assert_not_awaited()
    finally:
        asyncio.run(server._memorybridge_service.close())
