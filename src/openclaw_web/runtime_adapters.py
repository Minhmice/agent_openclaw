"""Small adapters that keep runtime capabilities on repository boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openclaw_web.db import Repository, connect, migrate
from openclaw_web.db.repository import ReviewProjectRecord
from openclaw_web.delivery.components import ComponentSetRecord, component_record
from openclaw_web.delivery.workflow_adapter import WorkflowCoordinatorAdapter
from openclaw_web.models import DeliveryRecord
from openclaw_web.pipeline.production import ProductionProject


class SqliteRunLocks:
    """Lock repository with one SQLite connection per calling thread."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _apply(self, method: str, *args: object) -> object:
        connection = connect(self.path)
        try:
            migrate(connection)
            return getattr(Repository(connection), method)(*args)
        finally:
            connection.close()

    def acquire_run_lock(self, key: str, owner: str, now: datetime, lease_seconds: int) -> bool:
        return bool(self._apply("acquire_run_lock", key, owner, now, lease_seconds))

    def renew_run_lock(self, key: str, owner: str, now: datetime, lease_seconds: int) -> bool:
        return bool(self._apply("renew_run_lock", key, owner, now, lease_seconds))

    def release_run_lock(self, key: str, owner: str) -> None:
        self._apply("release_run_lock", key, owner)


class RepositoryComponents:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def get_component_set(self, channel_id: str, message_id: str) -> ComponentSetRecord | None:
        stored = self.repository.get_component_set(channel_id, message_id)
        return None if stored is None else component_record(stored)

    def claim_component_action(self, component_set_id: str, actor_id: str, action: str) -> bool:
        claimed = self.repository.claim_component_action(component_set_id, actor_id, action)
        if not claimed:
            self.repository.reconcile_component_action(component_set_id, actor_id, action)
        return claimed

    def release_component_action(self, component_set_id: str, actor_id: str, action: str) -> None:
        self.repository.release_component_action(component_set_id, actor_id, action)


class RepositoryProduction:
    """Narrow production-pipeline adapters over the durable repository."""

    def __init__(self, repository: Repository, workflow_root: Path) -> None:
        self.repository = repository
        self.workflow_root = workflow_root

    def persist_project(self, project: ProductionProject) -> bool:
        inserted = self.repository.ensure_review_project(
            MutableReviewProject(
                project_id=project.project_id,
                candidate_id=project.candidate_id,
                market_id=project.market_id,
                artifact_dir=project.artifact_dir,
                created_at=project.created_at,
            )
        )
        source = Path(project.artifact_dir) / "workflow-project.json"
        coordinator = self.workflow_root / "workflow-coordinator.py"
        target = self.workflow_root / "projects" / project.project_id / "project.json"
        if not target.is_file():
            completed = WorkflowCoordinatorAdapter(coordinator).initialize(source)
            if completed.returncode != 0:
                raise RuntimeError("workflow coordinator rejected project initialization")
        return inserted

    def enqueue_delivery(self, delivery: DeliveryRecord) -> bool:
        return self.repository.enqueue_delivery_once(delivery)


@dataclass(slots=True)
class MutableReviewProject(ReviewProjectRecord):
    project_id: str
    candidate_id: str
    market_id: str
    artifact_dir: str
    created_at: datetime
