#!/bin/sh
set -eu

token_file="${MEMORYBRIDGE_MCP_TOKEN_FILE:-$HOME/.config/memorybridge.token}"
if [ ! -f "$token_file" ] || [ ! -r "$token_file" ]; then
    printf '%s\n' "MemoryBridge Codex wrapper: token file is missing" >&2
    exit 1
fi

mode="$(stat -c '%a' "$token_file")"
if [ $((0${mode} & 077)) -ne 0 ]; then
    printf '%s\n' "MemoryBridge Codex wrapper: token file must be owner-only" >&2
    exit 1
fi

if [ "$(stat -c '%u' "$token_file")" != "$(id -u)" ]; then
    printf '%s\n' "MemoryBridge Codex wrapper: token file owner does not match the process" >&2
    exit 1
fi

token="$(tr -d '\r\n' < "$token_file")"
if [ -z "$token" ]; then
    printf '%s\n' "MemoryBridge Codex wrapper: token file is empty" >&2
    exit 1
fi

export MEMORYBRIDGE_MCP_TOKEN="$token"
codex_bin="${MEMORYBRIDGE_CODEX_BIN:-$(command -v codex || true)}"
if [ -z "$codex_bin" ]; then
    printf '%s\n' "MemoryBridge Codex wrapper: codex executable was not found" >&2
    exit 1
fi
exec "$codex_bin" "$@"
