"""Transactional repositories for canonical workflow records."""

from __future__ import annotations

import sqlite3
from typing import Self

from openclaw_web.audit.store import AuditStore
from openclaw_web.db.base import (
    BaseStore,
    _immediate_transaction,
)
from openclaw_web.delivery.store import (
    DeliveryStore,
    ReviewProjectRecord,
)
from openclaw_web.discovery.store import (
    DiscoverySeedBatch,
    DiscoverySeedDisposition,
    DiscoverySeedUpsertResult,
    DiscoveryStore,
)
from openclaw_web.lead_intelligence.store import (
    DashboardActionReceipt,
    LeadStore,
)
from openclaw_web.platform.errors import (
    RepositoryConflict,
    RepositoryConflictError,
    RepositoryError,
    RunConfigMismatchError,
)

__all__ = [
    "AuditStore",
    "BaseStore",
    "DashboardActionReceipt",
    "DeliveryStore",
    "DiscoverySeedBatch",
    "DiscoverySeedDisposition",
    "DiscoverySeedUpsertResult",
    "DiscoveryStore",
    "LeadStore",
    "Repository",
    "RepositoryConflict",
    "RepositoryConflictError",
    "RepositoryError",
    "ReviewProjectRecord",
    "RunConfigMismatchError",
    "_immediate_transaction",
]


class Repository(DiscoveryStore, AuditStore, LeadStore, DeliveryStore):
    """Explicit-transaction persistence for immutable workflow records.

    Inherits domain operations across discovery, audit, lead intelligence,
    and delivery while composing each domain store cleanly.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def close(self) -> None:
        """Close the owned SQLite connection."""
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def discovery(self) -> DiscoveryStore:
        return self

    @property
    def audit(self) -> AuditStore:
        return self

    @property
    def lead_intelligence(self) -> LeadStore:
        return self

    @property
    def lead(self) -> LeadStore:
        return self

    @property
    def delivery(self) -> DeliveryStore:
        return self
