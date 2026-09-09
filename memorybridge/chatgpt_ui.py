from __future__ import annotations

ARCHIVE_RESOURCE_URI = "ui://memorybridge/archive.html"

ARCHIVE_UI_META = {
    "ui": {"resourceUri": ARCHIVE_RESOURCE_URI},
    # Keep the compatibility alias for ChatGPT hosts that have not switched to
    # the MCP Apps metadata name yet.
    "openai/outputTemplate": ARCHIVE_RESOURCE_URI,
}

ARCHIVE_WIDGET_META = {"ui": {"prefersBorder": True}}

ARCHIVE_WIDGET_HTML = r"""
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>MemoryBridge 归档</title>
    <style>
      :root {
        color-scheme: light dark;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      body { margin: 0; padding: 14px; color: CanvasText; background: Canvas; }
      .card { border: 1px solid color-mix(in srgb, CanvasText 16%, transparent); border-radius: 14px; padding: 16px; }
      .eyebrow { font-size: 12px; opacity: .68; margin-bottom: 5px; }
      h1 { font-size: 18px; margin: 0 0 8px; }
      p { font-size: 14px; line-height: 1.5; margin: 6px 0; }
      .status { min-height: 21px; font-size: 13px; opacity: .78; margin: 12px 0; }
      .success { color: #16803c; opacity: 1; }
      .error { color: #c62828; opacity: 1; }
      button {
        width: 100%; border: 0; border-radius: 10px; padding: 11px 14px;
        background: #0b72e7; color: white; font-size: 14px; font-weight: 650; cursor: pointer;
      }
      button:hover { background: #0864ca; }
      button:disabled { opacity: .55; cursor: wait; }
      .note { font-size: 11px; opacity: .58; margin-top: 10px; }
    </style>
  </head>
  <body>
    <section class="card" aria-labelledby="title">
      <div class="eyebrow">MemoryBridge</div>
      <h1 id="title">归档当前对话</h1>
      <p id="description">生成一条可跨会话复用的摘要，确认后保存到你的 MemoryBridge。</p>
      <div id="status" class="status" role="status" aria-live="polite"></div>
      <button id="archive" type="button">MemoryBridge归档</button>
      <div class="note">不会保存密码、令牌、密钥或完整会话原文。</div>
    </section>
    <script>
      const button = document.getElementById("archive");
      const status = document.getElementById("status");
      const title = document.getElementById("title");
      const description = document.getElementById("description");
      const pending = new Map();
      let requestId = 0;
      const archiveRequestId = globalThis.crypto?.randomUUID
        ? globalThis.crypto.randomUUID()
        : `chatgpt-archive-${Date.now()}-${Math.random().toString(16).slice(2)}`;

      function setStatus(message, className = "") {
        status.textContent = message;
        status.className = `status ${className}`.trim();
      }

      function request(method, params) {
        const id = ++requestId;
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        return new Promise((resolve, reject) => {
          pending.set(id, { resolve, reject });
          window.setTimeout(() => {
            if (!pending.has(id)) return;
            pending.delete(id);
            reject(new Error("ChatGPT 没有及时响应"));
          }, 30000);
        });
      }

      function resultFrom(params) {
        return params?.structuredContent ?? params?.result?.structuredContent ?? params;
      }

      function render(result) {
        if (!result || typeof result !== "object") return;
        if (result.title) title.textContent = String(result.title);
        if (result.description) description.textContent = String(result.description);
        if (result.stored === true) {
          button.disabled = true;
          button.textContent = "已保存到 MemoryBridge";
          setStatus(`写入成功${result.id ? ` · ${String(result.id).slice(0, 12)}…` : ""}`, "success");
          return;
        }
        if (result.stored === false && result.error) {
          button.disabled = false;
          button.textContent = "重试 MemoryBridge归档";
          setStatus(String(result.error), "error");
        }
      }

      window.addEventListener("message", (event) => {
        if (event.source !== window.parent) return;
        const message = event.data;
        if (!message || message.jsonrpc !== "2.0") return;
        if (message.id !== undefined && pending.has(message.id)) {
          const item = pending.get(message.id);
          pending.delete(message.id);
          if (message.error) item.reject(message.error);
          else item.resolve(message.result);
          return;
        }
        if (message.method === "ui/notifications/tool-result") {
          render(resultFrom(message.params));
        }
      }, { passive: true });

      async function askChatGPTToArchive() {
        const prompt = [
          "请归档当前对话到 MemoryBridge。",
          "先根据当前对话生成一条简洁、可跨会话复用的摘要，只保留持久的决定、偏好、项目状态、已完成事项和下一步。",
          "绝对不要写入密码、API key、bearer token、私钥、cookie、验证码、支付信息或完整会话原文。",
          `然后调用 memorybridge_archive_save，参数 summary 必须是摘要，source_agent 必须为 chatgpt，idempotency_key 必须为 ${archiveRequestId}；有标题、决定、下一步和项目名时一并传入。`,
          "只有工具返回 stored=true 才报告保存成功；不要改用普通 memory_put。",
        ].join(" ");
        try {
          // Standard MCP Apps bridge. ChatGPT's compatibility alias is kept as
          // a fallback for older hosts.
          await request("ui/message", {
            role: "user",
            content: [{ type: "text", text: prompt }],
          });
        } catch (error) {
          const openai = window.openai;
          if (!openai?.sendFollowUpMessage) throw error;
          await openai.sendFollowUpMessage({ prompt });
        }
      }

      button.addEventListener("click", async () => {
        button.disabled = true;
        setStatus("已请求 ChatGPT 生成摘要，正在保存…");
        try {
          await askChatGPTToArchive();
          setStatus("请求已发送；等待 MemoryBridge 返回保存结果…");
        } catch (error) {
          button.disabled = false;
          setStatus(error?.message || "无法发送归档请求，请重新连接 MemoryBridge。", "error");
        }
      });
    </script>
  </body>
</html>
""".strip()
