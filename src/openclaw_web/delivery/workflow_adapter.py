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

    def approve(self, project_id: str, actor_id: str, *, dry_run: bool = False) -> CoordinatorCommand:
        argv = [sys.executable, str(self.coordinator_path), "approve", project_id, "--actor", actor_id]
        if dry_run:
            argv.append("--dry-run")
        return CoordinatorCommand(argv)

    def execute(self, command: CoordinatorCommand) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command.argv, shell=False, check=False, capture_output=True, text=True, timeout=self.timeout_seconds)
