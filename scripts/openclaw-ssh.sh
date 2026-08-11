#!/usr/bin/env bash
set -u

: "${OPENCLAW_SSH_HOST:?Set OPENCLAW_SSH_HOST in the agent environment}"
: "${OPENCLAW_SSH_USER:?Set OPENCLAW_SSH_USER in the agent environment}"

port="${OPENCLAW_SSH_PORT:-22}"
if [[ ! "$port" =~ ^[0-9]+$ ]]; then
    printf '%s\n' 'OPENCLAW_SSH_PORT must be a numeric TCP port.' >&2
    exit 2
fi

ssh_args=(-p "$port")

if [[ -n "${OPENCLAW_SSH_KEY:-}" ]]; then
    if [[ ! -f "$OPENCLAW_SSH_KEY" ]]; then
        printf 'OPENCLAW_SSH_KEY does not point to a file: %s\n' "$OPENCLAW_SSH_KEY" >&2
        exit 2
    fi
    ssh_args+=(-i "$OPENCLAW_SSH_KEY")
fi

ssh_args+=("${OPENCLAW_SSH_USER}@${OPENCLAW_SSH_HOST}")
ssh_args+=("$@")

# Passwords are intentionally not read or passed as process arguments.
# Native OpenSSH will prompt interactively when no key/agent is available.
exec ssh "${ssh_args[@]}"
