"""Discovery capability view over the shared Repository connection."""

from __future__ import annotations

import sqlite3

from openclaw_web.db.repository import DiscoverySeedBatch, DiscoverySeedUpsertResult, Repository


class RepositoryDiscovery:
    """Thin ownership boundary; transactions remain owned by Repository."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @property
    def connection(self) -> sqlite3.Connection:
        return self.repository.connection

    def upsert_seed(self, batch: DiscoverySeedBatch, cohort: str) -> DiscoverySeedUpsertResult:
        return self.repository.upsert_discovery_seed(batch, cohort)


DiscoveryRepository = RepositoryDiscovery

__all__ = ["DiscoveryRepository", "RepositoryDiscovery"]
