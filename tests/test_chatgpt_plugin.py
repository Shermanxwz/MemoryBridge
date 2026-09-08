import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
BUILDER = REPO_ROOT / "scripts" / "build_chatgpt_plugin.py"


def run_builder(app_id: str, target: Path, *, marketplace: bool = False) -> subprocess.CompletedProcess[str]:
    destination_flag = "--marketplace-root" if marketplace else "--output"
    return subprocess.run(
        [sys.executable, str(BUILDER), "--app-id", app_id, destination_flag, str(target)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_plugin_builder_rejects_placeholders(tmp_path: Path):
    result = run_builder("asdk_app_YOUR_REAL_CHATGPT_APP_ID", tmp_path / "rejected")
    assert result.returncode != 0
    assert "placeholder app id" in result.stderr


def test_plugin_builder_rejects_unsupported_app_id_prefix(tmp_path: Path):
    result = run_builder("app_memorybridge_not_official_123", tmp_path / "rejected")
    assert result.returncode != 0
    assert "unsupported ChatGPT app id prefix" in result.stderr


def test_plugin_builder_emits_web_compatible_app_binding(tmp_path: Path):
    plugin = tmp_path / "plugin"
    result = run_builder("asdk_app_memorybridge_test_123", plugin)
    assert result.returncode == 0, result.stderr

    manifest = json.loads((plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    binding = json.loads((plugin / ".app.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "memorybridge"
    assert manifest["version"] == "0.2.0"
    assert manifest["apps"] == "./.app.json"
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert not (plugin / ".mcp.json").exists()
    assert binding == {
        "apps": {
            "memorybridge": {
                "id": "asdk_app_memorybridge_test_123",
                "required": True,
            }
        }
    }
    assert (plugin / "skills" / "memorybridge" / "SKILL.md").is_file()


def test_plugin_builder_normalizes_copied_plugin_technical_id(tmp_path: Path):
    plugin = tmp_path / "plugin"
    result = run_builder("plugin_asdk_app_memorybridge_test_456", plugin)
    assert result.returncode == 0, result.stderr
    binding = json.loads((plugin / ".app.json").read_text(encoding="utf-8"))
    assert binding["apps"]["memorybridge"]["id"] == "asdk_app_memorybridge_test_456"


def test_plugin_builder_emits_github_importable_marketplace(tmp_path: Path):
    root = tmp_path / "marketplace"
    result = run_builder("connector_memorybridge_test_123", root, marketplace=True)
    assert result.returncode == 0, result.stderr

    marketplace = json.loads(
        (root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    entry = marketplace["plugins"][0]
    assert entry["name"] == "memorybridge"
    assert entry["source"] == {"source": "local", "path": "./plugins/memorybridge"}
    assert entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
    assert (root / "plugins" / "memorybridge" / ".app.json").is_file()


def test_plugin_builder_cli_reports_target(tmp_path: Path):
    target = tmp_path / "cli-plugin"
    result = run_builder("templated_apps_memorybridge_cli_123", target)
    assert result.returncode == 0, result.stderr
    assert target.as_posix() in result.stdout
    assert (target / ".codex-plugin" / "plugin.json").is_file()
