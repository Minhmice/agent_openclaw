import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, get_ident

import pytest

import openclaw_web.db.migrations as migration_module
from openclaw_web.db.connection import connect as open_connection
from openclaw_web.db.connection import managed_connection
from openclaw_web.db.migrations import Migration, migrate
from openclaw_web.db.repository import Repository, RepositoryConflict
from openclaw_web.models import (
    CandidateSeed,
    ClaimStatus,
    Confidence,
    DeliveryRecord,
    DeliveryState,
    Evidence,
    FeedbackEvent,
    IssueRecord,
    ProjectState,
    ScoreRecord,
    Severity,
)

EXPECTED_TABLES = {
    "schema_migrations",
    "runs",
    "run_locks",
    "candidates",
    "candidate_sources",
    "pages",
    "evidence",
    "audits",
    "scores",
    "issues",
    "projects",
    "feedback",
    "deliveries",
    "component_sets",
    "worklog_events",
}
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


def _seed(url: str, name: str, address: str | None) -> CandidateSeed:
    return CandidateSeed(
        url=url,
        business_name=name,
        source_url="https://directory.example/source",
        source_type="directory",
        discovered_at=datetime(2026, 8, 12, tzinfo=UTC),
        address=address,
    )


def _evidence(*, evidence_id: str = "evidence-1", observed_value: str = "fast") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        candidate_id="candidate-1",
        page_url="https://example.com/",
        evidence_type="performance",
        observed_value=observed_value,
        claim_status=ClaimStatus.OBSERVED,
        confidence=Confidence.HIGH,
        captured_at=datetime(2026, 8, 12, tzinfo=UTC),
        content_hash="a" * 64,
        evidence_urls=["https://example.com/"],
    )


def _score(*, value: float = 80.0) -> ScoreRecord:
    return ScoreRecord(
        score_name="website-quality",
        score_value=value,
        rubric_version="v1",
        inputs={"lcp_ms": 1500},
        evidence_ids=["evidence-1"],
        deterministic=True,
        explanation_vi="Trang tải nhanh.",
        confidence=Confidence.HIGH,
    )


def _issue(*, title: str = "Thiếu CTA") -> IssueRecord:
    return IssueRecord(
        issue_id="issue-1",
        candidate_id="candidate-1",
        title=title,
        severity=Severity.P1,
        evidence_ids=["evidence-1"],
        recommendation_vi="Bổ sung CTA rõ ràng.",
        page_url="https://example.com/",
    )


def _delivery(*, delivery_id: str = "delivery-1", project_id: str = "project-1") -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=delivery_id,
        event_type="review-card",
        project_id=project_id,
        channel_id="channel-1",
        payload_path="artifacts/review.json",
        idempotency_key="review-card:project-1:v1",
        status=DeliveryState.PENDING,
    )


def _feedback(*, action: str = "approve") -> FeedbackEvent:
    return FeedbackEvent(
        event_id="feedback-1",
        event_type="review-decision",
        project_id="project-1",
        actor_id="actor-1",
        action=action,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        project_state=ProjectState.APPROVED,
    )


def test_connect_configures_sqlite_and_migrate_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.sqlite"
    db = connect(path)

    assert path.parent.is_dir()
    assert db.row_factory is sqlite3.Row
    assert db.isolation_level is None
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 1

    migrate(db)
    migrate(db)

    tables = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert EXPECTED_TABLES <= tables
    assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_connection_and_repository_contexts_close_the_connection(tmp_path: Path) -> None:
    with managed_connection(tmp_path / "managed.sqlite") as managed:
        migrate(managed)
        assert managed.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError):
        managed.execute("SELECT 1")

    owned = open_connection(tmp_path / "repository.sqlite")
    migrate(owned)
    with Repository(owned) as repo:
        assert repo.count_candidates() == 0
    with pytest.raises(sqlite3.ProgrammingError):
        owned.execute("SELECT 1")


def test_required_unique_indexes_are_present(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)

    unique_index_sql = "\n".join(
        row["sql"] or ""
        for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
        )
    ).lower()

    assert "canonical_domain" in unique_index_sql
    assert "idempotency_key" in unique_index_sql
    assert "channel_id" in unique_index_sql and "message_id" in unique_index_sql


def test_migration_failure_rolls_back_every_statement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = connect(tmp_path / "state.sqlite")
    original = migration_module._MIGRATIONS
    broken = Migration(
        version=1,
        statements=("CREATE TABLE partial_table (id INTEGER)", "NOT VALID SQL"),
    )
    monkeypatch.setattr(migration_module, "_MIGRATIONS", (broken,))

    with pytest.raises(sqlite3.OperationalError):
        migrate(db)

    assert not db.in_transaction
    assert db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name IN ('schema_migrations', 'partial_table')"
    ).fetchone()[0] == 0
    monkeypatch.setattr(migration_module, "_MIGRATIONS", original)
    migrate(db)
    assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1


def test_candidate_domain_is_deduplicated(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    first = repo.upsert_candidate("https://www.example.com", "Example Co", "Hà Nội")
    second = repo.upsert_candidate("https://example.com/", "Example Co", "Hà Nội")

    assert first.candidate_id == second.candidate_id
    assert repo.count_candidates() == 1


@pytest.mark.parametrize(
    ("first_url", "second_url", "canonical_domain"),
    [
        ("https://WWW.Example.COM.:443/a", "https://example.com/b", "example.com"),
        ("http://shop.example.co.uk:80", "https://blog.example.co.uk", "example.co.uk"),
        ("https://bücher.de", "https://xn--bcher-kva.de/", "xn--bcher-kva.de"),
    ],
)
def test_domain_normalization_deduplicates_safe_registrable_domains(
    tmp_path: Path,
    first_url: str,
    second_url: str,
    canonical_domain: str,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    first = repo.upsert_candidate(first_url, "Example", "Hà Nội")
    second = repo.upsert_candidate(second_url, "Example", "Hà Nội")

    assert first.candidate_id == second.candidate_id
    assert first.canonical_domain == canonical_domain


def test_distinct_domains_remain_distinct(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    first = repo.upsert_candidate("https://example.com", "Example", "Hà Nội")
    second = repo.upsert_candidate("https://example.net", "Other", "Hà Nội")

    assert first.candidate_id != second.candidate_id
    assert repo.count_candidates() == 2


def test_private_suffix_tenants_remain_distinct(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    first = repo.upsert_candidate("https://alpha.github.io", "Alpha", "Hà Nội")
    second = repo.upsert_candidate("https://beta.github.io", "Beta", "Hà Nội")

    assert first.candidate_id != second.candidate_id
    assert first.canonical_domain == "alpha.github.io"
    assert second.canonical_domain == "beta.github.io"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "https://user:password@example.com",
        "https:///missing-host",
        "not-a-url",
    ],
)
def test_candidate_rejects_invalid_or_credentialed_urls(tmp_path: Path, url: str) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    with pytest.raises(ValueError):
        repo.upsert_candidate(url, "Example", "Hà Nội")

    assert repo.count_candidates() == 0


def test_find_duplicate_falls_back_to_normalized_name_and_address(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    existing = repo.upsert_candidate(
        "https://first-example.com", "Công ty Ánh Dương.", "12, Phố Huế"
    )

    duplicate = repo.find_duplicate(
        _seed("https://unrelated-example.net", "  CÔNG TY ÁNH DƯƠNG ", "12 Phố Huế")
    )

    assert duplicate is not None
    assert duplicate.candidate_id == existing.candidate_id

    persisted = repo.upsert_candidate(
        "https://unrelated-example.net", "CÔNG TY ÁNH DƯƠNG", "12 Phố Huế"
    )
    assert persisted.candidate_id == existing.candidate_id
    alias = repo.find_duplicate(_seed("https://unrelated-example.net", "Renamed", None))
    assert alias is not None
    assert alias.candidate_id == existing.candidate_id


def test_concurrent_candidate_upsert_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    db = connect(path)
    migrate(db)
    db.close()
    barrier = Barrier(2)

    def upsert(url: str) -> str:
        connection = connect(path)
        try:
            barrier.wait(timeout=5)
            return Repository(connection).upsert_candidate(url, "Example", "Hà Nội").candidate_id
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(
            executor.map(upsert, ["https://www.example.com/a", "https://example.com/b"])
        )

    check = connect(path)
    assert ids[0] == ids[1]
    assert Repository(check).count_candidates() == 1


def test_create_or_resume_run_is_idempotent_and_rejects_config_mismatch(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    first = repo.create_or_resume_run("hanoi-80km:2026-08-12", "config-v1")
    second = repo.create_or_resume_run("hanoi-80km:2026-08-12", "config-v1")

    assert first == second
    assert first.idempotency_key == "hanoi-80km:2026-08-12"
    assert first.config_version == "config-v1"
    assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    with pytest.raises(RepositoryConflict):
        repo.create_or_resume_run("hanoi-80km:2026-08-12", "config-v2")
    assert not db.in_transaction


@pytest.mark.parametrize(
    ("table", "identity_column", "identity", "record", "changed"),
    [
        (
            "evidence",
            "evidence_id",
            "evidence-1",
            _evidence(),
            _evidence(observed_value="slow"),
        ),
        ("issues", "issue_id", "issue-1", _issue(), _issue(title="CTA chưa rõ")),
        ("feedback", "event_id", "feedback-1", _feedback(), _feedback(action="reject")),
    ],
)
def test_append_records_are_idempotent_and_conflicting_identity_is_rejected(
    tmp_path: Path,
    table: str,
    identity_column: str,
    identity: str,
    record: Evidence | IssueRecord | FeedbackEvent,
    changed: Evidence | IssueRecord | FeedbackEvent,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    append = {
        "evidence": repo.append_evidence,
        "issues": repo.append_issue,
        "feedback": repo.append_feedback,
    }[table]

    append(record)  # type: ignore[arg-type]
    append(record)  # type: ignore[arg-type]

    assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    snapshot = db.execute(
        f"SELECT snapshot_json FROM {table} WHERE {identity_column} = ?", (identity,)
    ).fetchone()[0]
    assert json.loads(snapshot)
    type(record).model_validate_json(snapshot)
    with pytest.raises(RepositoryConflict):
        append(changed)  # type: ignore[arg-type]
    assert not db.in_transaction


def test_score_append_uses_deterministic_identity_and_rejects_changed_result(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    repo.append_score(_score())
    repo.append_score(_score())

    row = db.execute("SELECT record_id, snapshot_json FROM scores").fetchone()
    assert len(row["record_id"]) == 64
    ScoreRecord.model_validate_json(row["snapshot_json"])
    with pytest.raises(RepositoryConflict):
        repo.append_score(_score(value=70.0))
    assert db.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 1


def test_delivery_enqueue_is_idempotent_by_unique_key_and_returns_persisted_record(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    delivery = _delivery()

    first = repo.enqueue_delivery(delivery)
    second = repo.enqueue_delivery(delivery)

    assert first == delivery
    assert second == delivery
    snapshot = db.execute("SELECT snapshot_json FROM deliveries").fetchone()[0]
    assert DeliveryRecord.model_validate_json(snapshot) == delivery
    with pytest.raises(RepositoryConflict):
        repo.enqueue_delivery(_delivery(delivery_id="delivery-2", project_id="project-2"))
    assert db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1


def test_declared_foreign_keys_are_enforced(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO candidate_sources (
                candidate_id, source_url, canonical_domain, source_type, discovered_at,
                snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "missing",
                "https://example.com/",
                "example.com",
                "directory",
                "2026-08-12T00:00:00.000000+00:00",
                "{}",
            ),
        )
