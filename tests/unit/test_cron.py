from datetime import UTC, date, datetime

from openclaw_web.pipeline.cron import CronRunner


class Locks:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired
        self.released = False
    def acquire_run_lock(self, *args: object) -> bool: return self.acquired
    def renew_run_lock(self, *args: object) -> bool: return True
    def release_run_lock(self, *args: object) -> None: self.released = True


def test_overlapping_cron_run_exits_as_skipped() -> None:
    result = CronRunner(Locks(False), lambda: "candidate-posted", now=lambda: datetime(2026, 8, 12, tzinfo=UTC)).run(date(2026, 8, 12))
    assert result.status == "skipped-overlap"
    assert result.exit_code == 0


def test_cron_releases_its_leased_lock_after_run() -> None:
    locks = Locks(True)
    result = CronRunner(locks, lambda: "no-candidate-defensible", now=lambda: datetime(2026, 8, 12, tzinfo=UTC)).run(date(2026, 8, 12))
    assert result.status == "no-candidate-defensible"
    assert result.exit_code == 0
    assert locks.released
