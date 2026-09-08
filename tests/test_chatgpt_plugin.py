import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_chatgpt_plugin import build_marketplace, build_plugin, validate_app_id


REPO_ROOT = Path(__file__).parents[1]


def test_plugin_builder_rejects_placeholders():
    with pytest.raises(ValueError):
        validate_app_id("YOUR_REAL_CHATGPT_APP_ID")


def test_plugin_builder_emits_web_compatible_app_binding(tmp_path: Path):
    plugin = build_plugin("app_memorybridge_test_123", tmp_path / "plugin", REPO_ROOT)
    manifest = json.loads((plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    binding = json.loads((plugin / ".app.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "memorybridge"
    assert manifest["version"] == "0.2.0"
    assert manifest["apps"] == "./.app.json"
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert not (plugin / ".mcp.json").exists()
    assert binding == {"apps": {"memorybridge": {"id": "app_memorybridge_test_123"}}}
    assert (plugin / "skills" / "memorybridge" / "SKILL.md").is_file()


def test_plugin_builder_emits_github_importable_marketplace(tmp_path: Path):
    root = build_marketplace("app_memorybridge_test_123", tmp_path / "marketplace", REPO_ROOT)
    marketplace = json.loads(
        (root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    entry = marketplace["plugins"][0]
    assert entry["name"] == "memorybridge"
    assert entry["source"] == {"source": "local", "path": "./plugins/memorybridge"}
    assert entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
    assert (root / "plugins" / "memorybridge" / ".app.json").is_file()


def test_plugin_builder_cli(tmp_path: Path):
    target = tmp_path / "cli-plugin"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "build_chatgpt_plugin.py"),
            "--app-id",
            "app_memorybridge_cli_123",
            "--output",
            str(target),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert target.as_posix() in result.stdout
    assert (target / ".codex-plugin" / "plugin.json").is_file()
