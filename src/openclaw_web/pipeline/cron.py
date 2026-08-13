"""Bounded daily discovery runner with a leased database lock."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime


@dataclass(frozen=True, slots=True)
class CronResult:
    status: str
    exit_code: int

class CronRunner:
    LEASE_SECONDS = 180 * 60
    def __init__(self, repository: object, discover: Callable[[], str], *, market_id: str = "hanoi-80km", now: Callable[[], datetime] | None = None) -> None:
        self.repository, self.discover, self.market_id, self.now = repository, discover, market_id, now or (lambda: datetime.now(UTC))
    def run(self, schedule_date: date) -> CronResult:
        key, owner, now = f"{self.market_id}:{schedule_date.isoformat()}", str(uuid.uuid4()), self.now()
        if not self.repository.acquire_run_lock(key, owner, now, self.LEASE_SECONDS):
            return CronResult("skipped-overlap", 0)
        try:
            status = self.discover()
            if status not in {"candidate-posted", "no-candidate-defensible", "partial", "failed"}:
                status = "failed"
            return CronResult(status, 1 if status == "failed" else 0)
        except Exception:  # noqa: BLE001 - a scheduled boundary must return a stable exit status
            return CronResult("failed", 1)
        finally:
            self.repository.release_run_lock(key, owner)
