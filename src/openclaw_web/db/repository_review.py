"""Review and portfolio capability view over the shared Repository connection."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from openclaw_web.db.repository import Repository
from openclaw_web.lead_contracts import PortfolioEntry


class RepositoryReview:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    @property
    def connection(self) -> sqlite3.Connection:
        return self.repository.connection

    def create_portfolio(
        self,
        *,
        portfolio_id: str,
        run_id: str | None,
        status: str,
        entries: tuple[PortfolioEntry, ...] | list[PortfolioEntry],
        target: int = 5,
        maximum: int = 7,
        created_at: datetime | None = None,
    ) -> bool:
        return self.repository.create_portfolio(
            portfolio_id=portfolio_id,
            run_id=run_id,
            status=status,
            entries=entries,
            target=target,
            maximum=maximum,
            created_at=created_at,
        )

    def get_portfolio(self, portfolio_id: str) -> dict[str, Any] | None:
        return self.repository.get_portfolio(portfolio_id)

    def get_portfolio_entry(self, entry_id: str) -> PortfolioEntry | None:
        return self.repository.get_portfolio_entry(entry_id)


ReviewRepository = RepositoryReview

__all__ = ["RepositoryReview", "ReviewRepository"]
