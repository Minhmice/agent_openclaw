from __future__ import annotations

import sys

from openclaw_web.delivery.workflow_adapter import WorkflowCoordinatorAdapter


def test_workflow_adapter_uses_argument_list_not_shell(tmp_path) -> None:
    command = WorkflowCoordinatorAdapter(tmp_path / "workflow-coordinator.py").approve(
        "project-1", "620891893659598850", dry_run=True
    )

    assert command.argv[:3] == [sys.executable, str(tmp_path / "workflow-coordinator.py"), "approve"]
    assert command.shell is False

