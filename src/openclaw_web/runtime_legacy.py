"""Legacy Curie review bridge capability boundary (backward compatibility shim)."""

from __future__ import annotations

from openclaw_web.delivery.legacy import (
    DEFAULT_DISCORD_GUILD_ID,
    DEFAULT_DISCORD_REVIEW_CHANNEL_ID,
    legacy_artifact_root,
    legacy_created_at,
    run_legacy_review,
)

__all__ = [
    "DEFAULT_DISCORD_GUILD_ID",
    "DEFAULT_DISCORD_REVIEW_CHANNEL_ID",
    "legacy_artifact_root",
    "legacy_created_at",
    "run_legacy_review",
]
