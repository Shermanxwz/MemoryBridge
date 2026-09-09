"""Optional ChatGPT MCP Apps UI for user-confirmed MemoryBridge archiving."""

from __future__ import annotations

ARCHIVE_RESOURCE_URI = "ui://memorybridge/archive-v3.html"
# Keep previous URIs alive so cached ChatGPT cards continue to load the fixed
# direct-call UI while the refreshed tool snapshot uses the newest URI.
ARCHIVE_PREVIOUS_RESOURCE_URI = "ui://memorybridge/archive-v2.html"
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

# The save tool is intentionally callable from the review card after the
# user's click. Keep the standard MCP Apps visibility and the ChatGPT
# compatibility flag so both bridge variants can authorize that call.
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
    <p id="description">先生成一条可跨会话复用的摘要，确认后保存到你的 MemoryBridge。</p>
    <div id="summary" class="summary" hidden></div>
    <div id="decisions" class="section" hidden></div>
    <div id="next-steps" class="section" hidden></div>
    <p class="muted">只保存摘要、决定和下一步；不会保存密码、bearer token、令牌、密钥或完整会话原文。</p>
    <button id="archive" type="button" disabled>等待摘要草稿</button>
    <div id="status" class="status">正在等待摘要草稿…</div>
  </section>
  <script>
    const title = document.getElementById("title");
    const description = document.getElementById("description");
    const summary = document.getElementById("summary");
    const decisions = document.getElementById("decisions");
    const nextSteps = document.getElementById("next-steps");
    const button = document.getElementById("archive");
    const status = document.getElementById("status");
    const archiveRequestId = "chatgpt-archive-" + Date.now() + "-" +
      Math.random().toString(16).slice(2);
    let draft = null;
    let busy = false;

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
          draft = {
            summary: text,
            title: typeof result.title === "string" ? result.title : "",
            decisions: asArray(result.decisions),
            next_steps: asArray(result.next_steps),
            project: typeof result.project === "string" ? result.project : "",
            session_id: typeof result.session_id === "string" ? result.session_id : "",
            idempotency_key: typeof result.idempotency_key === "string"
              ? result.idempotency_key : ""
          };
          title.textContent = draft.title || "归档当前对话";
          description.textContent = "摘要草稿已生成。点击后直接保存，不会发送新的对话消息。";
          summary.textContent = draft.summary;
          summary.hidden = false;
          renderList(decisions, "已确定", draft.decisions);
          renderList(nextSteps, "下一步", draft.next_steps);
          button.disabled = false;
          button.textContent = "确认归档";
          setStatus("请确认摘要内容后保存。");
        }
      }

      if (result.state === "draft_required") {
        draft = null;
        summary.hidden = true;
        button.disabled = true;
        button.textContent = "等待摘要草稿";
        setStatus("摘要草稿尚未生成，请重新调用归档卡片。", "error");
      }

      if (result.stored === true) {
        busy = false;
        button.disabled = true;
        button.textContent = "已归档";
        setStatus("已保存到 MemoryBridge。", "ok");
      } else if (result.stored === false && result.error) {
        busy = false;
        button.disabled = !draft;
        button.textContent = draft ? "确认归档" : "等待摘要草稿";
        setStatus("未保存：" + String(result.error), "error");
      }
    }

    function request(method, params) {
      return new Promise(function (resolve, reject) {
        const id = "archive-" + Date.now() + "-" + Math.random().toString(16).slice(2);
        let settled = false;
        const timer = setTimeout(function () {
          if (!settled) {
            settled = true;
            reject(new Error("请求超时"));
          }
        }, 30000);
        function onMessage(event) {
          const message = event.data || {};
          if (message.id !== id) return;
          window.removeEventListener("message", onMessage);
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          if (message.error) reject(new Error(message.error.message || "请求失败"));
          else resolve(message.result);
        }
        window.addEventListener("message", onMessage);
        window.parent.postMessage({
          jsonrpc: "2.0",
          id: id,
          method: method,
          params: params || {}
        }, "*");
      });
    }

    async function callTool(name, args) {
      // MCP Apps is the portable path and is the recommended ChatGPT path.
      // Keep the ChatGPT alias as a fallback for older hosts.
      try {
        return await request("tools/call", {name: name, arguments: args});
      } catch (bridgeError) {
        if (window.openai && typeof window.openai.callTool === "function") {
          return window.openai.callTool(name, args);
        }
        throw bridgeError;
      }
    }

    function resultFrom(value) {
      if (!value || typeof value !== "object") return value;
      return value.structuredContent || value.structured_content || value;
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

    button.addEventListener("click", async function () {
      if (busy || !draft || !draft.summary) return;
      busy = true;
      button.disabled = true;
      setStatus("正在直接保存摘要…");
      const args = {
        summary: draft.summary,
        idempotency_key: draft.idempotency_key || archiveRequestId
      };
      if (draft.title) args.title = draft.title;
      if (draft.decisions.length) args.decisions = draft.decisions;
      if (draft.next_steps.length) args.next_steps = draft.next_steps;
      if (draft.project) args.project = draft.project;
      if (draft.session_id) args.session_id = draft.session_id;
      try {
        render(resultFrom(await callTool("memorybridge_archive_save", args)));
      } catch (error) {
        busy = false;
        button.disabled = false;
        const detail = error && error.message ? "：" + error.message : "，请稍后重试";
        setStatus("未保存" + detail + "。", "error");
      }
    });
  </script>
</body>
</html>
"""
