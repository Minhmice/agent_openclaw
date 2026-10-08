"""Shared configuration helpers for the runtime capability boundaries."""

from __future__ import annotations

import os
from pathlib import Path


def state_db() -> Path:
    configured = os.environ.get("OPENCLAW_WEB_STATE_DB")
    return (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".local/state/openclaw-web/state.sqlite"
    )


def workflow_root() -> Path:
    configured = os.environ.get("OPENCLAW_WORKFLOW_ROOT")
    return (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".openclaw/workflow"
    )


def required_discord_guild_id() -> str:
    configured = os.environ.get("OPENCLAW_WEB_DISCORD_GUILD_ID", "")
    if not configured:
        raise RuntimeError("OPENCLAW_WEB_DISCORD_GUILD_ID is not configured")
    return configured
