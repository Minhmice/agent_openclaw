from __future__ import annotations

import sys

import pytest

from openclaw_web.delivery.workflow_adapter import WorkflowCoordinatorAdapter


def adapter(tmp_path) -> WorkflowCoordinatorAdapter:
    return WorkflowCoordinatorAdapter(tmp_path / "workflow-coordinator.py")


def test_workflow_adapter_approve_has_no_unsupported_dry_run(tmp_path) -> None:
    command = adapter(tmp_path).approve("project-1", "620891893659598850")

    assert command.argv == [
        sys.executable,
        str(tmp_path / "workflow-coordinator.py"),
        "lead-approve",
        "project-1",
        "--actor",
        "620891893659598850",
    ]
    assert command.shell is False

    with pytest.raises(TypeError):
        adapter(tmp_path).approve(  # type: ignore[call-arg]
            "project-1", "620891893659598850", dry_run=True
        )


@pytest.mark.parametrize(
    ("action", "page_slug", "reason", "arguments"),
    [
        ("reject", None, "not ready", ["lead-reject", "project-1", "not ready"]),
        (
            "request-changes",
            None,
            "add proof",
            ["lead-request-change", "project-1", "add proof"],
        ),
        ("page-status", "home", None, ["page-status", "project-1", "home"]),
        ("mark-done", "home", None, ["page-done", "project-1", "home"]),
        ("page-approve", "home", None, ["page-approve", "project-1", "home"]),
        ("block", "home", "missing copy", ["block", "project-1", "home", "missing copy"]),
        ("final-confirm", None, None, ["final-confirm", "project-1"]),
    ],
)
def test_workflow_adapter_maps_button_actions_to_canonical_subcommands(
    tmp_path, action: str, page_slug: str | None, reason: str | None, arguments: list[str]
) -> None:
    command = adapter(tmp_path).action(
        project_id="project-1",
        action=action,
        actor_id="actor-1",
        page_slug=page_slug,
        reason=reason,
    )

    assert command.argv == [
        sys.executable,
        str(tmp_path / "workflow-coordinator.py"),
        *arguments,
        "--actor",
        "actor-1",
    ]
    assert command.shell is False


@pytest.mark.parametrize(
    ("action", "page_slug", "reason"),
    [
        ("reject", None, None),
        ("request-changes", None, None),
        ("page-status", None, None),
        ("mark-done", None, None),
        ("page-approve", None, None),
        ("block", "home", None),
    ],
)
def test_workflow_adapter_rejects_missing_action_context(
    tmp_path, action: str, page_slug: str | None, reason: str | None
) -> None:
    with pytest.raises(ValueError, match="requires"):
        adapter(tmp_path).action(
            project_id="project-1",
            action=action,
            actor_id="actor-1",
            page_slug=page_slug,
            reason=reason,
        )
