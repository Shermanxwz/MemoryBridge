import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const spoolRoot = mkdtempSync(join(tmpdir(), "memorybridge-openclaw-"));
process.env.MEMORYBRIDGE_SPOOL_DIR = spoolRoot;

const moduleUrl = pathToFileURL(new URL("../../integrations/openclaw/index.js", import.meta.url).pathname).href;
const { default: plugin } = await import(moduleUrl);
const handlers = new Map();
plugin.register({
  on(name, callback) {
    handlers.set(name, callback);
  },
});

if (!handlers.has("message_received") || !handlers.has("agent_end")) {
  throw new Error("MemoryBridge did not register required OpenClaw hooks");
}

await handlers.get("message_received")(
  { content: "seal user message", messageId: "m-user", runId: "run-1" },
  { sessionKey: "seal-openclaw" },
);
await handlers.get("agent_end")(
  {
    runId: "run-1",
    messages: [
      { role: "user", content: "seal user message" },
      { role: "assistant", content: "seal assistant response" },
    ],
  },
  { sessionKey: "seal-openclaw" },
);

const pending = join(spoolRoot, "pending");
const files = readdirSync(pending).filter((name) => name.endsWith(".json"));
if (files.length !== 2) {
  throw new Error(`expected 2 durable spool files, got ${files.length}`);
}
const rows = files.map((name) => JSON.parse(readFileSync(join(pending, name), "utf8")));
const roles = new Set(rows.map((row) => row.role));
if (!roles.has("user") || !roles.has("assistant")) {
  throw new Error(`unexpected captured roles: ${JSON.stringify([...roles])}`);
}
if (!rows.every((row) => row.source_agent === "openclaw" && row.session_id === "seal-openclaw")) {
  throw new Error("OpenClaw capture metadata mismatch");
}
console.log("OpenClaw real-package hook contract: PASS");
