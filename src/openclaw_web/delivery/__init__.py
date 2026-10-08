"""Discord delivery adapters and capability boundaries."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .openclaw_transport import OpenClawAgentTransport, SentMessage
from .outbox import OutboxWorker
from .workflow_adapter import WorkflowCoordinatorAdapter


def run_component_action(text: str, *args: Any, **kwargs: Any) -> dict[str, str]:
    from .runners import run_component_action as _impl

    runtime = sys.modules.get("openclaw_web.runtime_components")
    if runtime is not None and getattr(runtime, "run_component_action", None) is not None:
        target = runtime.run_component_action
        if target is not run_component_action and target is not _impl:
            return target(text, *args, **kwargs)
    return _impl(text, *args, **kwargs)


def run_component_callback(text: str, *args: Any, **kwargs: Any) -> dict[str, str]:
    from .runners import run_component_callback as _impl

    runtime = sys.modules.get("openclaw_web.runtime_components")
    if runtime is not None and getattr(runtime, "run_component_callback", None) is not None:
        target = runtime.run_component_callback
        if target is not run_component_callback and target is not _impl:
            return target(text, *args, **kwargs)
    return _impl(text, *args, **kwargs)


def run_legacy_review(
    project_id: str,
    *args: Any,
    workflow_root: Path | None = None,
    review_channel: str | None = None,
    guild_id: str | None = None,
    artifact_root: Path | None = None,
    state_db_path: Path | None = None,
    transport_factory: Any = OpenClawAgentTransport,
    **kwargs: Any,
) -> dict[str, object]:
    from .legacy import run_legacy_review as _impl

    runtime = sys.modules.get("openclaw_web.runtime_legacy")
    if runtime is not None and getattr(runtime, "run_legacy_review", None) is not None:
        target = runtime.run_legacy_review
        if target is not run_legacy_review and target is not _impl:
            call_kwargs: dict[str, Any] = {}
            if workflow_root is not None:
                call_kwargs["workflow_root"] = workflow_root
            if review_channel is not None:
                call_kwargs["review_channel"] = review_channel
            if guild_id is not None:
                call_kwargs["guild_id"] = guild_id
            if artifact_root is not None:
                call_kwargs["artifact_root"] = artifact_root
            if state_db_path is not None:
                call_kwargs["state_db_path"] = state_db_path
            if transport_factory is not OpenClawAgentTransport:
                call_kwargs["transport_factory"] = transport_factory
            call_kwargs.update(kwargs)
            return target(project_id, *args, **call_kwargs)

    return _impl(
        project_id,
        *args,
        workflow_root=workflow_root,
        review_channel=review_channel,
        guild_id=guild_id,
        artifact_root=artifact_root,
        state_db_path=state_db_path,
        transport_factory=transport_factory,
        **kwargs,
    )


__all__ = [
    "OpenClawAgentTransport",
    "OutboxWorker",
    "SentMessage",
    "WorkflowCoordinatorAdapter",
    "run_component_action",
    "run_component_callback",
    "run_legacy_review",
]
