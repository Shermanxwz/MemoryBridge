from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="memorybridge-hermes-"))
os.environ["HOME"] = str(root)
os.environ["HERMES_HOME"] = str(root / ".hermes")
os.environ["MEMORYBRIDGE_SPOOL_DIR"] = str(root / "spool")

from hermes_state import SessionDB  # noqa: E402

session_id = "seal-hermes-session"
db = SessionDB()
try:
    db.create_session(session_id=session_id, source="cli", model="seal-model")
    db.append_message(session_id=session_id, role="user", content="seal hermes user message")
    db.append_message(session_id=session_id, role="assistant", content="seal hermes assistant response")
    db.end_session(session_id, end_reason="seal")
finally:
    close = getattr(db, "close", None)
    if callable(close):
        close()

plugin_path = Path("integrations/hermes/__init__.py").resolve()
spec = importlib.util.spec_from_file_location(
    "memorybridge_hermes_contract",
    plugin_path,
    submodule_search_locations=[str(plugin_path.parent)],
)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load MemoryBridge Hermes plugin")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
plugin.on_session_finalize(session_id=session_id, source="cli", model="seal-model")

pending = root / "spool" / "pending"
files = sorted(pending.glob("*.json"))
if len(files) != 2:
    raise RuntimeError(f"expected 2 Hermes spool files, got {len(files)}")
rows = [json.loads(path.read_text(encoding="utf-8")) for path in files]
roles = {row["role"] for row in rows}
if roles != {"user", "assistant"}:
    raise RuntimeError(f"unexpected Hermes roles: {roles}")
if not all(row["source_agent"] == "hermes" and row["session_id"] == session_id for row in rows):
    raise RuntimeError("Hermes capture metadata mismatch")
print("Hermes real SessionDB capture contract: PASS")
