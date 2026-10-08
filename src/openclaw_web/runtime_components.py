"""Discord component action and callback capability boundary (backward compatibility shim)."""

from __future__ import annotations

from openclaw_web.delivery.runners import (
    DEFAULT_DISCORD_GUILD_ID,
    CoordinatorActions,
    bounded_project_value,
    canonical_callback_envelope,
    component_gate_ready,
    component_read_only,
    component_result_message,
    has_unresolved_priority,
    project_snapshot_text,
    run_component_action,
    run_component_callback,
    strict_callback_json,
    workflow_project,
)

__all__ = [
    "DEFAULT_DISCORD_GUILD_ID",
    "CoordinatorActions",
    "bounded_project_value",
    "canonical_callback_envelope",
    "component_gate_ready",
    "component_read_only",
    "component_result_message",
    "has_unresolved_priority",
    "project_snapshot_text",
    "run_component_action",
    "run_component_callback",
    "strict_callback_json",
    "workflow_project",
]
