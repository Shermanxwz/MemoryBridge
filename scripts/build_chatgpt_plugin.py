from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

PLUGIN_NAME = "memorybridge"
PLUGIN_VERSION = "0.2.0"
APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.~:-]{3,256}$")
PLACEHOLDER_MARKERS = ("YOUR_", "REPLACE_", "PLACEHOLDER")


def validate_app_id(app_id: str) -> str:
    value = app_id.strip()
    if not APP_ID_PATTERN.fullmatch(value):
        raise ValueError("app id contains unsupported characters or has an invalid length")
    upper = value.upper()
    if any(marker in upper for marker in PLACEHOLDER_MARKERS):
        raise ValueError("refusing to build a deployable Plugin with a placeholder app id")
    return value


def plugin_manifest() -> dict:
    return {
        "name": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "description": "Durable cross-session memory workflows backed by the approved MemoryBridge ChatGPT app.",
        "author": {
            "name": "MemoryBridge contributors",
            "url": "https://github.com/Shermanxwz/MemoryBridge",
        },
        "homepage": "https://github.com/Shermanxwz/MemoryBridge/tree/main/integrations/chatgpt",
        "repository": "https://github.com/Shermanxwz/MemoryBridge",
        "license": "MIT",
        "keywords": ["memory", "mcp", "chatgpt", "work", "codex", "durable-context"],
        "skills": "./skills/",
        "apps": "./.app.json",
        "interface": {
            "displayName": "MemoryBridge",
            "shortDescription": "Durable memory across ChatGPT, Work, Codex, Hermes, and OpenClaw.",
            "longDescription": (
                "Retrieve durable project context and persist selected decisions, preferences, and outcomes through "
                "an approved MemoryBridge app without treating every conversation as an automatically captured transcript."
            ),
            "developerName": "MemoryBridge contributors",
            "category": "Productivity",
            "capabilities": ["Interactive", "Read", "Write"],
            "websiteURL": "https://github.com/Shermanxwz/MemoryBridge",
            "privacyPolicyURL": "https://github.com/Shermanxwz/MemoryBridge/blob/main/PRIVACY.md",
            "termsOfServiceURL": "https://github.com/Shermanxwz/MemoryBridge/blob/main/TERMS.md",
            "defaultPrompt": [
                "Use MemoryBridge to find durable context relevant to this task before continuing.",
                "Save the durable decisions from this task to MemoryBridge for the next approved agent.",
            ],
        },
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_plugin(app_id: str, output: Path, source_root: Path) -> Path:
    value = validate_app_id(app_id)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    write_json(output / ".codex-plugin" / "plugin.json", plugin_manifest())
    write_json(output / ".app.json", {"apps": {PLUGIN_NAME: {"id": value}}})

    source_skill = source_root / "integrations" / "chatgpt" / "skill" / "memorybridge"
    if not (source_skill / "SKILL.md").is_file():
        raise FileNotFoundError(f"missing source skill: {source_skill / 'SKILL.md'}")
    shutil.copytree(source_skill, output / "skills" / "memorybridge")

    if (output / ".mcp.json").exists():
        raise RuntimeError("generated ChatGPT Plugin must reference the approved app, not declare .mcp.json")
    return output


def build_marketplace(app_id: str, root: Path, source_root: Path) -> Path:
    plugin_dir = root / "plugins" / PLUGIN_NAME
    build_plugin(app_id, plugin_dir, source_root)
    marketplace = {
        "name": "memorybridge",
        "interface": {"displayName": "MemoryBridge"},
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }
        ],
    }
    write_json(root / ".agents" / "plugins" / "marketplace.json", marketplace)
    return root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a ChatGPT/Codex Plugin package bound to an existing approved MemoryBridge app."
    )
    parser.add_argument("--app-id", required=True, help="Real ChatGPT workspace app/connector id")
    destinations = parser.add_mutually_exclusive_group(required=True)
    destinations.add_argument("--output", type=Path, help="Plugin package output directory")
    destinations.add_argument(
        "--marketplace-root",
        type=Path,
        help="Output a GitHub-importable marketplace tree containing the Plugin",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_root = Path(__file__).resolve().parents[1]
    if args.output is not None:
        target = build_plugin(args.app_id, args.output.resolve(), source_root)
    else:
        target = build_marketplace(args.app_id, args.marketplace_root.resolve(), source_root)
    print(target)


if __name__ == "__main__":
    main()
