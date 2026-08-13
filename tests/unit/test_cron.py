from __future__ import annotations

from datetime import UTC, date, datetime
from threading import Event

from openclaw_web.pipeline.cron import CronRunner


class Locks:
    def __init__(self, acquired: bool = True, *, renewed: bool = True) -> None:
        self.acquired = acquired
        self.renewed = renewed
        self.acquire_calls: list[tuple[object, ...]] = []
        self.renew_calls: list[tuple[object, ...]] = []
        self.release_calls: list[tuple[str, str]] = []
        self.renew_event = Event()

    def acquire_run_lock(self, *args: object) -> bool:
        self.acquire_calls.append(args)
        return self.acquired

    def renew_run_lock(self, *args: object) -> bool:
        self.renew_calls.append(args)
        self.renew_event.set()
        return self.renewed

    def release_run_lock(self, key: str, owner: str) -> None:
        self.release_calls.append((key, owner))


class ReleaseFailureLocks(Locks):
    def release_run_lock(self, key: str, owner: str) -> None:
        super().release_run_lock(key, owner)
        raise RuntimeError("database unavailable")


NOW = datetime(2026, 8, 12, tzinfo=UTC)


def test_overlapping_cron_run_exits_as_skipped_without_release() -> None:
    locks = Locks(False)

    result = CronRunner(locks, lambda: "candidate-posted", now=lambda: NOW).run(
        date(2026, 8, 12)
    )

    assert result.status == "skipped-overlap"
    assert result.exit_code == 0
    assert len(locks.acquire_calls) == 1
    assert locks.release_calls == []


def test_cron_renews_lease_while_discovery_runs_then_releases_same_owner() -> None:
    locks = Locks()
    renewed_at = datetime(2026, 8, 12, 1, tzinfo=UTC)
    clock_values = iter((NOW, renewed_at))

    def discover() -> str:
        assert locks.renew_event.wait(timeout=1)
        return "no-candidate-defensible"

    result = CronRunner(
        locks,
        discover,
        now=lambda: next(clock_values),
        renew_interval_seconds=0.001,
    ).run(date(2026, 8, 12))

    assert result.status == "no-candidate-defensible"
    assert result.exit_code == 0
    assert locks.renew_calls
    acquired_key, acquired_owner, *_ = locks.acquire_calls[0]
    assert locks.renew_calls[0][2] == renewed_at
    assert locks.release_calls == [(acquired_key, acquired_owner)]


def test_cron_reports_failed_if_lease_renewal_is_lost_and_still_releases() -> None:
    locks = Locks(renewed=False)

    def discover() -> str:
        assert locks.renew_event.wait(timeout=1)
        return "candidate-posted"

    result = CronRunner(
        locks,
        discover,
        now=lambda: NOW,
        renew_interval_seconds=0.001,
    ).run(date(2026, 8, 12))

    assert result.status == "failed"
    assert result.exit_code == 1
    assert len(locks.release_calls) == 1


def test_cron_releases_its_lease_when_discovery_raises() -> None:
    locks = Locks()

    def fail() -> str:
        raise RuntimeError("provider failed")

    result = CronRunner(locks, fail, now=lambda: NOW).run(date(2026, 8, 12))

    assert result.status == "failed"
    assert result.exit_code == 1
    assert len(locks.release_calls) == 1


def test_cron_reports_failed_if_lock_release_cannot_be_verified() -> None:
    locks = ReleaseFailureLocks()

    result = CronRunner(locks, lambda: "candidate-posted", now=lambda: NOW).run(
        date(2026, 8, 12)
    )

    assert result.status == "failed"
    assert result.exit_code == 1
    assert len(locks.release_calls) == 1
