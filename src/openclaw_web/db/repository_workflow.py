"""Workflow/action capability view over the shared Repository connection."""

from __future__ import annotations

import sqlite3
from typing import Any

from openclaw_web.db.repository import DashboardActionReceipt, Repository


class RepositoryWorkflow:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @property
    def connection(self) -> sqlite3.Connection:
        return self.repository.connection

    def claim_dashboard_action(
        self,
        *,
        idempotency_key: str,
        action: str,
        target_id: str,
        actor_id: str,
        expected_state_version: int,
    ) -> DashboardActionReceipt:
        return self.repository.claim_dashboard_action(
            idempotency_key=idempotency_key,
            action=action,
            target_id=target_id,
            actor_id=actor_id,
            expected_state_version=expected_state_version,
        )

    def complete_dashboard_action(
        self,
        idempotency_key: str,
        *,
        status: str,
        response: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> DashboardActionReceipt:
        return self.repository.complete_dashboard_action(
            idempotency_key,
            status=status,
            response=response,
            error_code=error_code,
        )


WorkflowRepository = RepositoryWorkflow

__all__ = ["RepositoryWorkflow", "WorkflowRepository"]
