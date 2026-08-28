import { createHash } from "node:crypto";
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, renameSync, writeFileSync } from "node:fs";
import { homedir, hostname } from "node:os";
import { join } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

function expandHome(value) {
  if (!value) return join(homedir(), ".memorybridge", "spool");
  return value.startsWith("~/") ? join(homedir(), value.slice(2)) : value;
}

function fsyncDir(path) {
  try {
    const fd = openSync(path, "r");
    try { fsyncSync(fd); } finally { closeSync(fd); }
  } catch {
    // Directory fsync is best-effort on platforms/filesystems that do not support it.
  }
}

function spool(role, content, sessionId) {
  if (!content) return;
  const contentDigest = createHash("sha256").update(content).digest("hex");
  const idempotencyKey = `openclaw:${sessionId ?? ""}:${role}:${contentDigest}`;
  const safeId = createHash("sha256").update(idempotencyKey).digest("hex");
  const root = expandHome(process.env.MEMORYBRIDGE_SPOOL_DIR);
  const pending = join(root, "pending");
  const sent = join(root, "sent");
  mkdirSync(pending, { recursive: true });
  mkdirSync(sent, { recursive: true });
  const target = join(pending, `${safeId}.json`);
  if (existsSync(target) || existsSync(join(sent, `${safeId}.json`))) return;
  const tmp = join(pending, `.${safeId}.${process.pid}.tmp`);
  const payload = JSON.stringify({
    content,
    source_agent: "openclaw",
    source_device: hostname(),
    session_id: sessionId ?? null,
    role,
    idempotency_key: idempotencyKey,
  });
  const fd = openSync(tmp, "wx", 0o600);
  try {
    writeFileSync(fd, payload, "utf8");
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(tmp, target);
  fsyncDir(pending);
}

export default definePluginEntry({
  id: "memorybridge",
  name: "MemoryBridge Capture",
  register(api) {
    api.on("message_received", async (event, ctx) => {
      spool("user", String(event?.content ?? ""), ctx?.sessionKey ?? event?.sessionId);
    });
    api.on("agent_end", async (event, ctx) => {
      const messages = Array.isArray(event?.messages) ? event.messages : [];
      const last = [...messages].reverse().find((m) => m?.role === "assistant");
      const content = typeof last?.content === "string" ? last.content : JSON.stringify(last?.content ?? "");
      spool("assistant", content, ctx?.sessionKey ?? event?.sessionId);
    });
  },
});
