"""Bounded daily discovery runner with a leased database lock."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import Event, Thread
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CronResult:
    status: str
    exit_code: int


class RunLockRepository(Protocol):
    def acquire_run_lock(self, key: str, owner: str, now: datetime, lease_seconds: int) -> bool: ...
    def renew_run_lock(self, key: str, owner: str, now: datetime, lease_seconds: int) -> bool: ...
    def release_run_lock(self, key: str, owner: str) -> None: ...


class CronRunner:
    LEASE_SECONDS = 180 * 60

    def __init__(
        self,
        repository: RunLockRepository,
        discover: Callable[[], str],
        *,
        market_id: str = "hanoi-80km",
        now: Callable[[], datetime] | None = None,
        renew_interval_seconds: float | None = None,
    ) -> None:
        interval = self.LEASE_SECONDS / 3 if renew_interval_seconds is None else renew_interval_seconds
        if isinstance(interval, bool) or not isinstance(interval, int | float) or interval <= 0:
            raise ValueError("renew_interval_seconds must be positive")
        self.repository = repository
        self.discover = discover
        self.market_id = market_id
        self.now = now or (lambda: datetime.now(UTC))
        self.renew_interval_seconds = float(interval)

    def run(self, schedule_date: date) -> CronResult:
        key, owner, now = f"{self.market_id}:{schedule_date.isoformat()}", str(uuid.uuid4()), self.now()
        if not self.repository.acquire_run_lock(key, owner, now, self.LEASE_SECONDS):
            return CronResult("skipped-overlap", 0)
        stopped = Event()
        lease_lost = Event()

        def renew() -> None:
            while not stopped.wait(self.renew_interval_seconds):
                try:
                    renewed = self.repository.renew_run_lock(
                        key, owner, self.now(), self.LEASE_SECONDS
                    )
                except Exception:  # noqa: BLE001 - lease loss must fail the boundary
                    renewed = False
                if not renewed:
                    lease_lost.set()
                    return

        renewer = Thread(target=renew, name="openclaw-web-lock-renewer", daemon=True)
        renewer.start()
        result = CronResult("failed", 1)
        try:
            status = self.discover()
            if lease_lost.is_set():
                status = "failed"
            if status not in {"candidate-posted", "no-candidate-defensible", "partial", "failed"}:
                status = "failed"
            result = CronResult(status, 1 if status == "failed" else 0)
        except Exception:  # noqa: BLE001 - a scheduled boundary must return a stable exit status
            result = CronResult("failed", 1)
        finally:
            stopped.set()
            renewer.join()
            try:
                self.repository.release_run_lock(key, owner)
            except Exception:  # noqa: BLE001 - unverifiable release fails the boundary
                result = CronResult("failed", 1)
        return result
