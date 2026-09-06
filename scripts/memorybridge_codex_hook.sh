#!/bin/sh
set -eu

# Load the persistent client configuration for normal operation, but never
# override an explicitly supplied spool directory. This matters for isolated
# capture tests and for operators who intentionally redirect the hook.
if [ -z "${MEMORYBRIDGE_SPOOL_DIR+x}" ]; then
    env_file=${MEMORYBRIDGE_ENV_FILE:-"${HOME}/.config/memorybridge.env"}
    if [ -r "$env_file" ]; then
        set -a
        . "$env_file"
        set +a
    fi
fi

hook_bin=${MEMORYBRIDGE_CODEX_HOOK_BIN:-"${HOME}/.local/share/memorybridge/venv/bin/memorybridge-codex-hook"}
exec "$hook_bin" "$@"
