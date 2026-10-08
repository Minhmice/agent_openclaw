"""Delivery capability view over the shared Repository connection."""

from __future__ import annotations

import sqlite3
from typing import Any

from openclaw_web.db.repository import Repository
from openclaw_web.models import DeliveryRecord


class RepositoryDelivery:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @property
    def connection(self) -> sqlite3.Connection:
        return self.repository.connection

    def record_portfolio_delivery(
        self,
        *,
        portfolio_id: str,
        delivery_id: str,
        idempotency_key: str,
        status: str,
        snapshot: dict[str, Any] | None = None,
    ) -> bool:
        return self.repository.record_portfolio_delivery(
            portfolio_id=portfolio_id,
            delivery_id=delivery_id,
            idempotency_key=idempotency_key,
            status=status,
            snapshot=snapshot,
        )

    def enqueue(self, delivery: DeliveryRecord) -> bool:
        return self.repository.enqueue_delivery_once(delivery)


DeliveryRepository = RepositoryDelivery

__all__ = ["DeliveryRepository", "RepositoryDelivery"]
