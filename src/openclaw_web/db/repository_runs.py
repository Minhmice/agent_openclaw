"""Run and lock capability view over the shared Repository connection."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from openclaw_web.db.repository import Repository
from openclaw_web.models import RunRecord


class RepositoryRuns:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @property
    def connection(self) -> sqlite3.Connection:
        return self.repository.connection

    def create_or_resume(self, idempotency_key: str, config_version: str) -> RunRecord:
        return self.repository.create_or_resume_run(idempotency_key, config_version)

    def acquire_lock(self, key: str, owner: str, now: datetime, lease_seconds: int) -> bool:
        return self.repository.acquire_run_lock(key, owner, now, lease_seconds)

    def renew_lock(self, key: str, owner: str, now: datetime, lease_seconds: int) -> bool:
        return self.repository.renew_run_lock(key, owner, now, lease_seconds)

    def release_lock(self, key: str, owner: str) -> None:
        self.repository.release_run_lock(key, owner)


RunRepository = RepositoryRuns

__all__ = ["RepositoryRuns", "RunRepository"]
