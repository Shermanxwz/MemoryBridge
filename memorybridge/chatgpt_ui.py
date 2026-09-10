"""Optional ChatGPT MCP Apps UI for explicit MemoryBridge archiving."""

from __future__ import annotations

ARCHIVE_RESOURCE_URI = "ui://memorybridge/archive-v7.html"
# Keep all previous URIs alive so cached ChatGPT cards continue to load the
# result-only UI while a refreshed tool snapshot uses the newest URI.
ARCHIVE_PREVIOUS_RESOURCE_URI = "ui://memorybridge/archive-v6.html"
ARCHIVE_PRIOR_RESOURCE_URI = "ui://memorybridge/archive-v5.html"
ARCHIVE_OLDER_RESOURCE_URI = "ui://memorybridge/archive-v4.html"
ARCHIVE_OLDEST_RESOURCE_URI = "ui://memorybridge/archive-v3.html"
ARCHIVE_V2_RESOURCE_URI = "ui://memorybridge/archive-v2.html"
ARCHIVE_LEGACY_RESOURCE_URI = "ui://memorybridge/archive.html"
# Some ChatGPT custom-app hosts qualify component tool calls with the
# connection slug before forwarding them to the MCP server.
ARCHIVE_HOST_TOOL_PREFIX = "memorybridge_mcp_archive."


def normalize_archive_host_tool_name(name: str) -> str:
    """Normalize ChatGPT's qualified component-tool names at the server edge.

    Some hosts send the connection slug as a normal MCP tool-name prefix;
    others preserve Markdown-style escaped underscores (``\\_``) when sending
    the component call. Both forms identify the same canonical tool and must
    never become separately registered tools.
    """
    if not isinstance(name, str):
        return name

    unescaped = name.replace(r"\_", "_")
    if unescaped.startswith(ARCHIVE_HOST_TOOL_PREFIX):
        return unescaped[len(ARCHIVE_HOST_TOOL_PREFIX) :]
    return name


ARCHIVE_UI_META = {
    "ui": {
        "resourceUri": ARCHIVE_RESOURCE_URI,
        "prefersBorder": True,
    },
    # ChatGPT currently accepts this compatibility alias as well.
    "openai/outputTemplate": ARCHIVE_RESOURCE_URI,
}

ARCHIVE_WIDGET_META = {"ui": {"prefersBorder": True}}

# Keep the compatibility save tool available to clients that cannot render a
# card. The normal archive-panel path writes server-side in one call, so the
# result card does not depend on a component-side tools/call.
ARCHIVE_SAVE_UI_META = {
    "ui": {"visibility": ["model", "app"]},
    "openai/widgetAccessible": True,
}

ARCHIVE_WIDGET_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body {
      margin: 0;
      padding: 16px;
      color: #1f2937;
      background: transparent;
    }
    .card {
      border: 1px solid #d7dce3;
      border-radius: 14px;
      padding: 16px;
      background: rgba(255, 255, 255, .96);
      box-shadow: 0 2px 8px rgba(15, 23, 42, .06);
    }
    h1 {
      margin: 0 0 6px;
      font-size: 17px;
    }
    p {
      margin: 6px 0;
      line-height: 1.5;
    }
    .muted {
      color: #667085;
      font-size: 13px;
    }
    .summary {
      margin: 13px 0;
      padding: 11px 12px;
      border-radius: 10px;
      background: #f4f6f8;
      white-space: pre-wrap;
      line-height: 1.55;
      font-size: 14px;
      max-height: 260px;
      overflow: auto;
    }
    .section {
      margin: 10px 0;
      font-size: 13px;
    }
    .section strong {
      display: block;
      margin-bottom: 4px;
    }
    ul {
      margin: 4px 0 0 18px;
      padding: 0;
    }
    li {
      margin: 3px 0;
      line-height: 1.4;
    }
    button {
      width: 100%;
      border: 0;
      border-radius: 10px;
      padding: 10px 14px;
      color: #fff;
      background: #111827;
      font-size: 14px;
      cursor: pointer;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: .48;
    }
    .status {
      min-height: 20px;
      margin-top: 9px;
      color: #667085;
      font-size: 13px;
    }
    .ok {
      color: #087443;
    }
    .error {
      color: #b42318;
    }
    @media (prefers-color-scheme: dark) {
      body { color: #eef2f6; }
      .card { border-color: #3a4350; background: rgba(25, 30, 38, .96); }
      .muted, .status { color: #aab4c0; }
      .summary { background: #202733; }
      button { background: #f3f4f6; color: #111827; }
    }
  </style>
</head>
<body>
  <section class="card" aria-live="polite">
    <h1 id="title">归档当前对话</h1>
    <p id="description">收到归档指令后，服务器会直接保存一条可跨会话复用的摘要。</p>
    <div id="summary" class="summary" hidden></div>
    <div id="decisions" class="section" hidden></div>
    <div id="next-steps" class="section" hidden></div>
    <p class="muted">只保存摘要、决定和下一步；不会保存密码、bearer token、令牌、密钥或完整会话原文。</p>
    <button id="archive" type="button" disabled>归档处理中</button>
    <div id="status" class="status">正在等待归档结果…</div>
  </section>
  <script>
    const title = document.getElementById("title");
    const description = document.getElementById("description");
    const summary = document.getElementById("summary");
    const decisions = document.getElementById("decisions");
    const nextSteps = document.getElementById("next-steps");
    const button = document.getElementById("archive");
    const status = document.getElementById("status");

    function setStatus(message, kind) {
      status.textContent = message || "";
      status.className = "status" + (kind ? " " + kind : "");
    }

    function asArray(value) {
      return Array.isArray(value) ? value.filter(function (item) {
        return typeof item === "string" && item.trim();
      }).slice(0, 20) : [];
    }

    function renderList(node, heading, values) {
      node.textContent = "";
      if (!values.length) {
        node.hidden = true;
        return;
      }
      const strong = document.createElement("strong");
      strong.textContent = heading;
      node.appendChild(strong);
      const list = document.createElement("ul");
      values.forEach(function (value) {
        const item = document.createElement("li");
        item.textContent = value;
        list.appendChild(item);
      });
      node.appendChild(list);
      node.hidden = false;
    }

    function candidateFrom(value) {
      if (!value || typeof value !== "object") return null;
      if (value.arguments && typeof value.arguments === "object") {
        return value.arguments;
      }
      if (value.input && typeof value.input === "object") {
        return value.input;
      }
      return value;
    }

    function render(value) {
      const result = candidateFrom(value);
      if (!result || typeof result !== "object") return;

      if (result.state === "draft" || typeof result.summary === "string") {
        const text = result.summary.trim();
        if (text) {
          title.textContent = typeof result.title === "string" && result.title
            ? result.title : "归档当前对话";
          description.textContent = typeof result.description === "string" && result.description
            ? result.description : "归档指令已收到，服务器正在保存。";
          summary.textContent = text;
          summary.hidden = false;
          renderList(decisions, "已确定", asArray(result.decisions));
          renderList(nextSteps, "下一步", asArray(result.next_steps));
          button.disabled = true;
          button.textContent = "归档处理中";
          setStatus("服务器正在保存归档…");
        }
      }

      if (result.state === "draft_required") {
        summary.hidden = true;
        button.disabled = true;
        button.textContent = "等待摘要草稿";
        setStatus("摘要未随归档指令提供，请重新发送 @MemoryBridge 归档。", "error");
      }

      if (result.stored === true) {
        button.disabled = true;
        button.textContent = "已归档";
        description.textContent = typeof result.description === "string" && result.description
          ? result.description : "已根据你的归档指令保存，不需要再次点击。";
        setStatus("已保存到 MemoryBridge。", "ok");
      } else if (result.stored === false && result.error) {
        button.disabled = true;
        button.textContent = "归档失败";
        setStatus("未保存：" + String(result.error) + "。请重新发送 @MemoryBridge 归档。", "error");
      }
    }

    function resultFrom(value) {
      if (!value || typeof value !== "object") return value;
      if (value.structuredContent || value.structured_content) {
        return value.structuredContent || value.structured_content;
      }
      if (value.result && typeof value.result === "object") {
        return resultFrom(value.result);
      }
      if (Array.isArray(value.content)) {
        const textItem = value.content.find(function (item) {
          return item && item.type === "text" && typeof item.text === "string";
        });
        if (textItem) {
          try {
            return JSON.parse(textItem.text);
          } catch (_) {
            // Fall through for hosts that expose only non-JSON text content.
          }
        }
      }
      return value;
    }

    window.addEventListener("message", function (event) {
      const message = event.data || {};
      if (message.method === "ui/notifications/tool-input") {
        render(message.params && (message.params.arguments ||
          message.params.input || message.params));
      }
      if (message.method === "ui/notifications/tool-result") {
        render(resultFrom(message.params && (message.params.result ||
          message.params)));
      }
    });

    if (window.openai && window.openai.toolInput) {
      render(window.openai.toolInput);
    }
    if (window.openai && window.openai.toolOutput) {
      render(resultFrom(window.openai.toolOutput));
    }

  </script>
</body>
</html>
"""
