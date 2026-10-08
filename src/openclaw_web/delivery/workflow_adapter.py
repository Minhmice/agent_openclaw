"""Argument-vector adapter for the canonical workflow coordinator."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CoordinatorCommand:
    argv: list[str]
    shell: bool = False


class WorkflowCoordinatorAdapter:
    def __init__(self, coordinator_path: Path, *, timeout_seconds: int = 45) -> None:
        self.coordinator_path, self.timeout_seconds = coordinator_path, timeout_seconds

    def approve(self, project_id: str, actor_id: str) -> CoordinatorCommand:
        return self.action(project_id=project_id, action="approve", actor_id=actor_id)

    def initialize(self, input_path: Path) -> subprocess.CompletedProcess[str]:
        if not input_path.is_file():
            raise FileNotFoundError("workflow project artifact does not exist")
        command = CoordinatorCommand(
            [sys.executable, str(self.coordinator_path), "init", "--input", str(input_path)]
        )
        return self.execute(command)

    def action(
        self,
        *,
        project_id: str,
        action: str,
        actor_id: str,
        page_slug: str | None = None,
        reason: str | None = None,
    ) -> CoordinatorCommand:
        command = {
            "select-lead": "lead-select",
            "watch-lead": "lead-watch",
            "approve": "lead-approve",
            "reject": "lead-reject",
            "request-changes": "lead-request-change",
            "page-status": "page-status",
            "mark-done": "page-done",
            "page-approve": "page-approve",
            "block": "block",
            "final-confirm": "final-confirm",
        }.get(action)
        if command is None:
            raise ValueError("unsupported component action")
        page_action = action in {"page-status", "mark-done", "page-approve", "block"}
        reason_action = action in {"reject", "request-changes", "block"}
        if page_action and not page_slug:
            raise ValueError(f"{action} requires page_slug")
        if reason_action and not reason:
            raise ValueError(f"{action} requires reason")

        argv = [sys.executable, str(self.coordinator_path), command, project_id]
        if page_action:
            argv.append(page_slug or "")
        if reason_action:
            argv.append(reason or "")
        argv.extend(("--actor", actor_id))
        return CoordinatorCommand(argv)

    def execute(self, command: CoordinatorCommand) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command.argv, shell=False, check=False, capture_output=True, text=True, timeout=self.timeout_seconds)
