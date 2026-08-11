import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, get_ident

import pytest

from openclaw_web.db.connection import connect as open_connection
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository

_OPEN_CONNECTIONS: list[sqlite3.Connection] = []
_TEST_THREAD_ID = get_ident()


def connect(path: str | Path) -> sqlite3.Connection:
    connection = open_connection(path)
    if get_ident() == _TEST_THREAD_ID:
        _OPEN_CONNECTIONS.append(connection)
    return connection


@pytest.fixture(autouse=True)
def close_test_connections() -> None:
    yield
    for connection in _OPEN_CONNECTIONS:
        connection.close()
    _OPEN_CONNECTIONS.clear()


def test_schedule_lock_rejects_overlap_and_reclaims_expired_lease(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    now = datetime.now(UTC)

    assert repo.acquire_run_lock("hanoi-80km:2026-08-12", "owner-a", now, 300)
    assert not repo.acquire_run_lock("hanoi-80km:2026-08-12", "owner-b", now, 300)
    assert repo.acquire_run_lock(
        "hanoi-80km:2026-08-12", "owner-b", now + timedelta(seconds=301), 300
    )


def test_lock_expiry_is_strict_and_same_owner_can_refresh(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    now = datetime(2026, 8, 12, tzinfo=UTC)

    assert repo.acquire_run_lock("schedule", "owner-a", now, 300)
    assert repo.acquire_run_lock("schedule", "owner-a", now + timedelta(seconds=100), 300)
    assert not repo.acquire_run_lock("schedule", "owner-b", now + timedelta(seconds=400), 300)
    assert repo.acquire_run_lock(
        "schedule", "owner-b", now + timedelta(seconds=400, microseconds=1), 300
    )


def test_lock_renew_and_release_require_matching_owner(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    now = datetime(2026, 8, 12, tzinfo=UTC)

    assert repo.acquire_run_lock("schedule", "owner-a", now, 60)
    assert not repo.renew_run_lock("schedule", "owner-b", now + timedelta(seconds=10), 60)
    repo.release_run_lock("schedule", "owner-b")
    assert not repo.acquire_run_lock("schedule", "owner-b", now + timedelta(seconds=10), 60)
    assert repo.renew_run_lock("schedule", "owner-a", now + timedelta(seconds=10), 60)
    assert not repo.renew_run_lock("schedule", "owner-a", now + timedelta(seconds=71), 60)
    repo.release_run_lock("schedule", "owner-a")
    assert repo.acquire_run_lock("schedule", "owner-b", now + timedelta(seconds=71), 60)


@pytest.mark.parametrize("lease_seconds", [0, -1, True])
def test_lock_rejects_invalid_lease(tmp_path: Path, lease_seconds: int) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    with pytest.raises(ValueError):
        repo.acquire_run_lock(
            "schedule", "owner", datetime(2026, 8, 12, tzinfo=UTC), lease_seconds
        )
    assert not db.in_transaction


def test_lock_rejects_naive_datetime(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    with pytest.raises(ValueError):
        repo.acquire_run_lock("schedule", "owner", datetime(2026, 8, 12), 60)  # noqa: DTZ001
    assert not db.in_transaction


def test_two_connections_race_for_one_lock_atomically(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    setup = connect(path)
    migrate(setup)
    setup.close()
    now = datetime(2026, 8, 12, tzinfo=UTC)
    barrier = Barrier(2)

    def acquire(owner: str) -> bool:
        db = connect(path)
        try:
            barrier.wait(timeout=5)
            return Repository(db).acquire_run_lock("schedule", owner, now, 60)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, ["owner-a", "owner-b"]))

    assert sorted(results) == [False, True]


def test_failed_lock_statement_rolls_back_and_connection_remains_usable(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    db.execute(
        """
        CREATE TRIGGER reject_locks BEFORE INSERT ON run_locks
        BEGIN
            SELECT RAISE(ABORT, 'rejected');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError):
        repo.acquire_run_lock(
            "schedule", "owner", datetime(2026, 8, 12, tzinfo=UTC), 60
        )

    assert not db.in_transaction
    assert db.execute("SELECT 1").fetchone()[0] == 1
