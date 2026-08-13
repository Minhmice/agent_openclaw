import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, get_ident
from types import SimpleNamespace
from typing import cast
from urllib.parse import quote

import pytest

import openclaw_web.db as db_package
import openclaw_web.db.connection as connection_module
import openclaw_web.db.migrations as migration_module
from openclaw_web.db.connection import (
    ConnectionConfigurationError,
    managed_connection,
)
from openclaw_web.db.connection import connect as open_connection
from openclaw_web.db.migrations import Migration, MigrationError, migrate
from openclaw_web.db.repository import (
    DiscoverySeedBatch,
    DiscoverySeedDisposition,
    Repository,
    RepositoryConflict,
    _immediate_transaction,
)
from openclaw_web.models import (
    Candidate,
    CandidateSeed,
    ClaimStatus,
    ComponentSet,
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
    "component_actions",
    "worklog_events",
}
_OPEN_CONNECTIONS: list[sqlite3.Connection] = []
_TEST_THREAD_ID = get_ident()


class _FailingConnection(sqlite3.Connection):
    fail_next_commit = False
    fail_next_rollback = False

    def commit(self) -> None:
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise sqlite3.OperationalError("injected commit failure")
        super().commit()

    def rollback(self) -> None:
        if self.fail_next_rollback:
            self.fail_next_rollback = False
            raise sqlite3.OperationalError("injected rollback failure")
        super().rollback()


def connect(path: str | Path) -> sqlite3.Connection:
    connection = open_connection(path)
    if get_ident() == _TEST_THREAD_ID:
        _OPEN_CONNECTIONS.append(connection)
    return connection


def _failing_connection(path: Path) -> _FailingConnection:
    connection = cast(
        _FailingConnection,
        sqlite3.connect(
            path,
            timeout=5.0,
            isolation_level=None,
            factory=_FailingConnection,
        ),
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
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


def test_discovery_batch_persists_each_actual_source_observation_with_its_timestamp(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    earlier = _seed("https://example.com", "Example result", None).validated_replace(
        source_type="serper",
        source_url="https://google.com/search?q=example",
        discovered_at=datetime(2026, 8, 12, 1, tzinfo=UTC),
        external_id="result-1",
        metadata={"provider": "serper"},
    )
    later = earlier.validated_replace(
        url="https://example.com/about",
        business_name="Example Company",
        source_type="google-places",
        source_url="https://maps.google.com/?cid=1",
        discovered_at=datetime(2026, 8, 12, 2, tzinfo=UTC),
        address="Hoàn Kiếm, Hà Nội",
        latitude=21.03,
        longitude=105.83,
        external_id="place-1",
        metadata={"provider": "google-places"},
    )

    result = Repository(db).upsert_discovery_seed(
        DiscoverySeedBatch(seed=later, observations=(earlier, later)), "local-service"
    )
    rows = db.execute(
        "SELECT source_type, discovered_at, snapshot_json FROM candidate_sources "
        "ORDER BY source_type"
    ).fetchall()

    assert result.disposition is DiscoverySeedDisposition.INSERTED
    assert len(rows) == 2
    snapshots = {row["source_type"]: json.loads(row["snapshot_json"]) for row in rows}
    assert snapshots["serper"]["candidate_seed"] == earlier.model_dump(mode="json")
    assert snapshots["serper"]["cohort"] == "local-service"
    assert snapshots["google-places"]["candidate_seed"] == later.model_dump(mode="json")
    assert {row["source_type"]: row["discovered_at"] for row in rows} == {
        "google-places": "2026-08-12T02:00:00.000000Z",
        "serper": "2026-08-12T01:00:00.000000Z",
    }


def test_discovery_batch_marks_only_each_contradictory_observation_as_conflict(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    existing = _seed("https://example.com", "Example", "Hà Nội")
    matching = existing.validated_replace(
        source_url="https://directory.example/matching",
        source_type="serper",
    )
    contradictory = matching.validated_replace(
        business_name="Different business",
        address="Đà Nẵng",
        source_url="https://directory.example/contradictory",
        source_type="google-places",
    )
    assert (
        repo.upsert_discovery_seed(existing, "other").disposition
        is DiscoverySeedDisposition.INSERTED
    )

    result = repo.upsert_discovery_seed(
        DiscoverySeedBatch(seed=matching, observations=(matching, contradictory)), "other"
    )
    rows = db.execute(
        "SELECT source_url, conflict, snapshot_json FROM candidate_sources "
        "WHERE source_url IN (?, ?) ORDER BY source_url",
        (str(matching.source_url), str(contradictory.source_url)),
    ).fetchall()

    assert result.disposition is DiscoverySeedDisposition.CONFLICT
    assert [
        (
            row["source_url"],
            row["conflict"],
            json.loads(row["snapshot_json"])["candidate_seed"]["business_name"],
        )
        for row in rows
    ] == [
        ("https://directory.example/contradictory", 1, "Different business"),
        ("https://directory.example/matching", 0, "Example"),
    ]


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


def _score(
    *, value: float = 80.0, evidence_ids: tuple[str, ...] = ("evidence-1",)
) -> ScoreRecord:
    return ScoreRecord(
        score_name="website-quality",
        score_value=value,
        rubric_version="v1",
        inputs={"lcp_ms": 1500},
        evidence_ids=list(evidence_ids),
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


def _insert_candidate_parent(db: sqlite3.Connection, candidate_id: str = "candidate-1") -> None:
    db.execute(
        """
        INSERT INTO candidates (
            candidate_id, canonical_domain, normalized_name, normalized_address,
            state, snapshot_json
        ) VALUES (?, ?, ?, NULL, ?, ?)
        """,
        (candidate_id, f"{candidate_id}.example", candidate_id, "discovered", "{}"),
    )


def _insert_run_parent(db: sqlite3.Connection, run_id: str = "run-1") -> None:
    db.execute(
        """
        INSERT INTO runs (
            run_id, idempotency_key, config_version, status, started_at, snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            f"key-{run_id}",
            "config-v1",
            "pending",
            "2026-08-12T00:00:00.000000Z",
            "{}",
        ),
    )


def _insert_project_parent(db: sqlite3.Connection, project_id: str = "project-1") -> None:
    db.execute(
        """
        INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json)
        VALUES (?, NULL, ?, 0, ?)
        """,
        (project_id, "new", "{}"),
    )


def test_db_package_exports_typed_persistence_errors() -> None:
    assert db_package.__all__ == [
        "ConnectionConfigurationError",
        "DiscoverySeedBatch",
        "DiscoverySeedDisposition",
        "DiscoverySeedUpsertResult",
        "MigrationError",
        "Repository",
        "RepositoryConflict",
        "RepositoryConflictError",
        "RepositoryError",
        "RunConfigMismatchError",
        "connect",
        "managed_connection",
        "migrate",
    ]
    assert db_package.ConnectionConfigurationError is ConnectionConfigurationError
    assert db_package.DiscoverySeedBatch is DiscoverySeedBatch
    assert db_package.MigrationError is MigrationError


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
    assert EXPECTED_TABLES == tables
    assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3
    migration_rows = db.execute(
        "SELECT version, checksum, applied_at FROM schema_migrations"
    ).fetchall()
    assert [row["version"] for row in migration_rows] == [1, 2, 3]
    for row, migration in zip(migration_rows, migration_module._MIGRATIONS, strict=True):
        assert row["checksum"] == migration.checksum
        assert len(row["checksum"]) == 64
        datetime.fromisoformat(row["applied_at"])
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_component_set_and_action_claim_are_durable(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repository = Repository(db)
    db.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-components', NULL, 'review', 4, '{}')"
    )
    record = ComponentSet(
        component_set_id="set-components",
        message_id="message-components",
        channel_id="channel-components",
        project_id="project-components",
        card_type="review",
        allowed_actions=["approve"],
        expires_at=datetime(2026, 8, 14, tzinfo=UTC),
        state_version=4,
        project_state=ProjectState.REVIEW,
    )

    repository.insert_component_set(record)

    loaded = repository.get_component_set("channel-components", "message-components")
    assert loaded == record
    assert repository.get_component_set_by_message_id("message-components") == record
    assert repository.get_component_set_by_message_id("missing-message") is None
    assert repository.get_project_state_version("project-components") == 4
    assert repository.claim_component_action("set-components", "actor", "approve")
    assert not repository.claim_component_action("set-components", "actor", "approve")


def test_component_set_message_lookup_fails_closed_when_identity_is_ambiguous(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repository = Repository(db)
    db.execute("DROP INDEX ux_component_sets_bot_message")
    db.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-ambiguous', NULL, 'review', 0, '{}')"
    )
    first = ComponentSet(
        component_set_id="set-ambiguous-a",
        message_id="message-ambiguous",
        channel_id="channel-a",
        project_id="project-ambiguous",
        card_type="review",
        allowed_actions=["approve"],
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        state_version=0,
        project_state=ProjectState.REVIEW,
    )
    second = first.validated_replace(
        component_set_id="set-ambiguous-b", channel_id="channel-b"
    )
    repository.insert_component_set(first)
    repository.insert_component_set(second)

    with pytest.raises(RepositoryConflict, match="ambiguous"):
        repository.get_component_set_by_message_id("message-ambiguous")


def test_confirmed_component_action_survives_release_and_reconciles_project(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repository = Repository(db)
    db.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-reconcile', NULL, 'review', 0, '{}')"
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="set-reconcile",
            message_id="message-reconcile",
            channel_id="channel-reconcile",
            project_id="project-reconcile",
            card_type="review",
            allowed_actions=["approve"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.REVIEW,
        )
    )
    assert repository.claim_component_action("set-reconcile", "actor", "approve")

    repository.confirm_component_action(
        "set-reconcile",
        "actor",
        "approve",
        state=ProjectState.APPROVED,
        state_version=1,
    )
    repository.release_component_action("set-reconcile", "actor", "approve")

    assert not repository.claim_component_action("set-reconcile", "actor", "approve")
    assert repository.reconcile_component_action("set-reconcile", "actor", "approve")
    row = db.execute(
        "SELECT state, state_version FROM projects WHERE project_id = 'project-reconcile'"
    ).fetchone()
    assert (row["state"], row["state_version"]) == ("approved", 1)


def test_connect_expands_tilde_once_for_sqlite_and_parent_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", os.fspath(home))
    monkeypatch.setenv("USERPROFILE", os.fspath(home))

    db = open_connection("~/nested/state.sqlite")
    try:
        expected = Path(os.path.abspath(os.path.expanduser("~/nested/state.sqlite")))
        assert expected.parent.is_dir()
        assert db.execute("PRAGMA database_list").fetchone()["file"] == os.fspath(expected)
    finally:
        db.close()


def test_connect_opens_readonly_uri_without_attempting_journal_mutation(tmp_path: Path) -> None:
    path = tmp_path / "readonly.sqlite"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE marker (id INTEGER)")
    raw.commit()
    raw.close()

    uri = f"file:{path.as_posix()}?mode=ro"
    db = open_connection(uri)
    try:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert db.execute("SELECT COUNT(*) FROM marker").fetchone()[0] == 0
    finally:
        db.close()


def test_connect_treats_immutable_uri_as_readonly(tmp_path: Path) -> None:
    path = tmp_path / "immutable.sqlite"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE marker (id INTEGER)")
    raw.commit()
    raw.close()

    db = open_connection(f"file:{path.as_posix()}?immutable=1")
    try:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        db.close()


def test_connect_parses_memory_mode_as_an_exact_query_parameter() -> None:
    db = open_connection("file:task3-memory?mode=memory&cache=shared")
    try:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    finally:
        db.close()


def test_connect_does_not_treat_query_values_containing_mode_memory_as_memory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "state.sqlite"
    uri = f"file:{path.as_posix()}?note=mode=memory"

    db = open_connection(uri)
    try:
        assert path.parent.is_dir()
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        db.close()


def test_connect_decodes_writable_uri_path_for_parent_creation(tmp_path: Path) -> None:
    path = tmp_path / "encoded parent" / "state.sqlite"
    uri = f"file:{quote(path.as_posix())}?mode=rwc"

    db = open_connection(uri)
    try:
        assert path.parent.is_dir()
        assert path.is_file()
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        db.close()


class _WalRefusingConnection(sqlite3.Connection):
    def execute(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> sqlite3.Cursor:
        if sql == "PRAGMA journal_mode = WAL":
            return super().execute("PRAGMA journal_mode = DELETE")
        return super().execute(sql, parameters)


def test_connect_rejects_writable_disk_when_wal_cannot_be_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[_WalRefusingConnection] = []
    sqlite_connect = sqlite3.connect

    def refusing_connect(*args: object, **kwargs: object) -> _WalRefusingConnection:
        connection = cast(
            _WalRefusingConnection,
            sqlite_connect(*args, **kwargs, factory=_WalRefusingConnection),
        )
        opened.append(connection)
        return connection

    monkeypatch.setattr(connection_module.sqlite3, "connect", refusing_connect)

    with pytest.raises(ConnectionConfigurationError, match="WAL"):
        open_connection(tmp_path / "state.sqlite")

    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


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


def _index_shapes(
    db: sqlite3.Connection, table: str
) -> dict[str, tuple[bool, tuple[str, ...], bool]]:
    shapes: dict[str, tuple[bool, tuple[str, ...], bool]] = {}
    for index in db.execute(f"PRAGMA index_list({table})").fetchall():
        name = str(index["name"])
        if name.startswith("sqlite_autoindex_"):
            continue
        columns = tuple(
            str(row["name"])
            for row in db.execute(f'PRAGMA index_info("{name}")').fetchall()
        )
        shapes[name] = (bool(index["unique"]), columns, bool(index["partial"]))
    return shapes


def test_required_indexes_have_exact_uniqueness_and_column_order(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)

    expected = {
        "runs": {"ux_runs_idempotency_key": (True, ("idempotency_key",), False)},
        "candidates": {
            "ux_candidates_canonical_domain": (True, ("canonical_domain",), False),
            "ux_candidates_name_address": (
                True,
                ("normalized_name", "normalized_address"),
                True,
            ),
        },
        "candidate_sources": {
            "ix_candidate_sources_domain": (False, ("canonical_domain",), False),
        },
        "evidence": {"ix_evidence_candidate_id": (False, ("candidate_id",), False)},
        "issues": {"ix_issues_candidate_id": (False, ("candidate_id",), False)},
        "feedback": {"ix_feedback_project_id": (False, ("project_id",), False)},
        "deliveries": {
            "ux_deliveries_idempotency_key": (True, ("idempotency_key",), False),
        },
        "component_sets": {
            "ux_component_sets_bot_message": (
                True,
                ("channel_id", "message_id"),
                False,
            ),
        },
        "worklog_events": {
            "ix_worklog_events_project_id": (False, ("project_id",), False),
        },
    }

    for table, indexes in expected.items():
        assert _index_shapes(db, table) == indexes


@pytest.mark.parametrize(
    "migrations",
    [
        cast(tuple[Migration, ...], [Migration(1, ("SELECT 1",))]),
        (),
        (Migration(0, ("SELECT 1",)),),
        (Migration(-1, ("SELECT 1",)),),
        (Migration(True, ("SELECT 1",)),),
        (Migration(1, ()),),
        (Migration(1, ("",)),),
        (Migration(1, ("   ",)),),
        (Migration(1, cast(tuple[str, ...], ("SELECT 1", 2))),),
        (Migration(1, cast(tuple[str, ...], ["SELECT 1"])),),
        (Migration(1, ("SELECT 1",)), Migration(1, ("SELECT 2",))),
        (Migration(2, ("SELECT 2",)), Migration(1, ("SELECT 1",))),
    ],
)
def test_migrate_rejects_invalid_definitions_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    migrations: tuple[Migration, ...],
) -> None:
    db = connect(tmp_path / "state.sqlite")
    monkeypatch.setattr(migration_module, "_MIGRATIONS", migrations)

    with pytest.raises(MigrationError, match="migration definitions"):
        migrate(db)

    assert db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    assert not db.in_transaction


def test_migrate_rejects_historical_sql_drift_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    before = db.total_changes
    historical = migration_module._MIGRATIONS[0]
    altered = Migration(historical.version, historical.statements + ("SELECT 1",))
    monkeypatch.setattr(
        migration_module,
        "_MIGRATIONS",
        (altered, *migration_module._MIGRATIONS[1:]),
    )

    with pytest.raises(MigrationError, match="checksum"):
        migrate(db)

    assert db.total_changes == before
    assert not db.in_transaction


def test_migrate_rejects_tampered_stored_checksum(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("UPDATE schema_migrations SET checksum = ? WHERE version = 1", ("0" * 64,))
    before = db.total_changes

    with pytest.raises(MigrationError, match="checksum"):
        migrate(db)

    assert db.total_changes == before
    assert not db.in_transaction


def test_migrate_rejects_unknown_applied_version(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute(
        "INSERT INTO schema_migrations (version, checksum, applied_at) VALUES (?, ?, ?)",
        (99, "0" * 64, "2026-08-12T00:00:00.000000Z"),
    )
    before = db.total_changes

    with pytest.raises(MigrationError, match="exact prefix"):
        migrate(db)

    assert db.total_changes == before


def test_migrate_rejects_applied_version_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrations = (
        Migration(1, ("CREATE TABLE one (id INTEGER)",)),
        Migration(2, ("CREATE TABLE two (id INTEGER)",)),
        Migration(3, ("CREATE TABLE three (id INTEGER)",)),
    )
    monkeypatch.setattr(migration_module, "_MIGRATIONS", migrations)
    db.execute(migration_module._SCHEMA_MIGRATIONS_SQL)
    for migration in (migrations[0], migrations[2]):
        db.execute(
            "INSERT INTO schema_migrations (version, checksum, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.checksum, "2026-08-12T00:00:00.000000Z"),
        )
    before = db.total_changes

    with pytest.raises(MigrationError, match="exact prefix"):
        migrate(db)

    assert db.total_changes == before
    assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'two'").fetchone()[0] == 0


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
    assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3


def test_migration_commit_failure_rolls_back_schema_and_connection_is_reusable(
    tmp_path: Path,
) -> None:
    db = _failing_connection(tmp_path / "state.sqlite")
    db.fail_next_commit = True

    with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
        migrate(db)

    assert not db.in_transaction
    assert db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0

    migrate(db)
    assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3


def test_migrate_rejects_caller_transaction_without_committing_it(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    db.execute("BEGIN IMMEDIATE")
    db.execute("CREATE TABLE caller_owned (id INTEGER)")

    with pytest.raises(MigrationError, match="transaction is active"):
        migrate(db)

    assert db.in_transaction
    db.rollback()
    assert db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'caller_owned'"
    ).fetchone()[0] == 0

    migrate(db)
    assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3


def test_deferred_foreign_key_commit_failure_rolls_back_repository_transaction(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("PRAGMA defer_foreign_keys = ON")

    with pytest.raises(sqlite3.IntegrityError), _immediate_transaction(db):
        db.execute(
            """
            INSERT INTO candidate_sources (
                observation_id, candidate_id, source_url, canonical_domain, source_type,
                discovered_at, conflict, snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "a" * 64,
                "missing",
                "https://example.com:8443/source",
                "example.com",
                "directory",
                "2026-08-12T00:00:00.000000Z",
                0,
                "{}",
            ),
        )

    assert not db.in_transaction
    assert db.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0] == 0

    candidate = Repository(db).upsert_candidate(
        "https://example.com:8443/recovered", "Recovered", None
    )
    assert candidate.canonical_domain == "example.com"


def test_injected_repository_commit_failure_rolls_back_and_connection_is_reusable(
    tmp_path: Path,
) -> None:
    db = _failing_connection(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    db.fail_next_commit = True

    with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
        repo.upsert_candidate("https://example.com:8443/failed", "Example", None)

    assert not db.in_transaction
    assert db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0] == 0

    recovered = repo.upsert_candidate("https://example.com:0/recovered", "Example", None)
    assert recovered.canonical_domain == "example.com"


def test_rollback_failure_does_not_mask_commit_failure(tmp_path: Path) -> None:
    db = _failing_connection(tmp_path / "state.sqlite")
    db.fail_next_commit = True
    db.fail_next_rollback = True

    with (
        pytest.raises(sqlite3.OperationalError, match="injected commit failure") as raised,
        _immediate_transaction(db),
    ):
        db.execute("CREATE TABLE partial_table (id INTEGER)")

    assert any(
        "injected rollback failure" in note
        for note in getattr(raised.value, "__notes__", ())
    )
    db.rollback()


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
        ("https://example.com:8443/a", "https://example.com:0/b", "example.com"),
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


def test_domain_deduplication_preserves_full_source_urls(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    first_url = "https://example.com:8443/a?source=one"
    second_url = "https://example.com:0/b?source=two"

    first = repo.upsert_candidate(first_url, "Example", None)
    second = repo.upsert_candidate(second_url, "Example", None)

    assert first.candidate_id == second.candidate_id
    assert str(first.website_url) == first_url
    assert [str(source_url) for source_url in first.source_urls] == [first_url]
    snapshot = Candidate.model_validate_json(
        db.execute(
            "SELECT snapshot_json FROM candidates WHERE candidate_id = ?",
            (first.candidate_id,),
        ).fetchone()[0]
    )
    assert str(snapshot.website_url) == first_url
    assert [str(source_url) for source_url in snapshot.source_urls] == [first_url]
    assert [
        row["source_url"]
        for row in db.execute(
            "SELECT source_url FROM candidate_sources ORDER BY source_url"
        ).fetchall()
    ] == sorted([first_url, second_url])


def test_candidate_sources_preserve_each_full_incoming_observation(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    first_url = "https://example.com:8443/a?nguon=mot"
    second_url = "https://example.com:9443/b?nguon=hai"

    repo.upsert_candidate(first_url, "Công ty Ánh Dương", "12 Phố Huế")
    repo.upsert_candidate(second_url, "CÔNG TY ÁNH DƯƠNG", "12, Phố Huế")

    rows = db.execute(
        """
        SELECT source_url, conflict, snapshot_json
        FROM candidate_sources ORDER BY source_url
        """
    ).fetchall()
    assert len(rows) == 2
    observations = {row["source_url"]: json.loads(row["snapshot_json"]) for row in rows}
    for observation in observations.values():
        discovered_at = observation.pop("discovered_at")
        datetime.fromisoformat(discovered_at)
    assert observations[first_url] == {
        "address": "12 Phố Huế",
        "business_name": "Công ty Ánh Dương",
        "candidate_id": observations[first_url]["candidate_id"],
        "canonical_domain": "example.com",
        "normalized_address": "12 phố huế",
        "normalized_name": "công ty ánh dương",
        "source_type": "direct",
        "source_url": first_url,
    }
    assert observations[second_url] == {
        "address": "12, Phố Huế",
        "business_name": "CÔNG TY ÁNH DƯƠNG",
        "candidate_id": observations[first_url]["candidate_id"],
        "canonical_domain": "example.com",
        "normalized_address": "12 phố huế",
        "normalized_name": "công ty ánh dương",
        "source_type": "direct",
        "source_url": second_url,
    }
    assert [row["conflict"] for row in rows] == [0, 0]


def test_contradictory_domain_observation_is_persisted_then_raises_conflict(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    repo.upsert_candidate("https://example.com/a", "Doanh nghiệp Một", "Hà Nội")

    with pytest.raises(RepositoryConflict, match="contradictory"):
        repo.upsert_candidate("https://example.com/b", "Doanh nghiệp Hai", "Đà Nẵng")

    assert not db.in_transaction
    conflict = db.execute(
        """
        SELECT conflict, snapshot_json FROM candidate_sources
        WHERE source_url = ?
        """,
        ("https://example.com/b",),
    ).fetchone()
    assert conflict["conflict"] == 1
    assert json.loads(conflict["snapshot_json"])["business_name"] == "Doanh nghiệp Hai"
    assert json.loads(conflict["snapshot_json"])["address"] == "Đà Nẵng"

    with pytest.raises(RepositoryConflict, match="contradictory"):
        repo.upsert_candidate("https://example.com/b", "Doanh nghiệp Hai", "Đà Nẵng")
    assert db.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1


def test_name_only_candidates_do_not_fallback_dedupe_and_blank_address_is_rejected(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    first = repo.upsert_candidate("https://first.example", "Tên giống nhau", None)
    second = repo.upsert_candidate("https://second.example", "Tên giống nhau", None)

    assert first.candidate_id != second.candidate_id
    with pytest.raises(ValueError, match="address"):
        repo.upsert_candidate("https://third.example", "Tên giống nhau", "   ")
    assert repo.count_candidates() == 2


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


def test_fallback_domain_alias_keeps_stable_candidate_identity_on_later_conflict(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    existing = repo.upsert_candidate(
        "https://first-example.com", "Công ty Ánh Dương", "12 Phố Huế"
    )
    alias = repo.upsert_candidate(
        "https://alias-example.net", "CÔNG TY ÁNH DƯƠNG", "12, Phố Huế"
    )
    assert alias.candidate_id == existing.candidate_id

    with pytest.raises(RepositoryConflict, match="contradictory"):
        repo.upsert_candidate("https://alias-example.net/about", "Doanh nghiệp Khác", "Huế")

    assert repo.count_candidates() == 1
    conflict = db.execute(
        """
        SELECT candidate_id, conflict FROM candidate_sources
        WHERE source_url = ?
        """,
        ("https://alias-example.net/about",),
    ).fetchone()
    assert tuple(conflict) == (existing.candidate_id, 1)


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


def test_discovery_seed_upsert_preserves_complete_observation_and_cohort(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    seed = _seed("https://example.com/about", "Example", "Hà Nội").validated_replace(
        source_url="https://maps.google.com/?cid=place-1",
        source_type="google-places",
        discovered_at=datetime(2026, 8, 12, 1, 2, 3, tzinfo=UTC),
        seed_id="seed-1",
        latitude=21.03,
        longitude=105.83,
        external_id="place-1",
        metadata={"provider": "google-places", "rank": 2},
    )

    result = Repository(db).upsert_discovery_seed(seed, "local-service")

    assert result.disposition is DiscoverySeedDisposition.INSERTED
    assert isinstance(result.candidate, Candidate)
    assert result.candidate.website_url == seed.url
    assert result.candidate.seed_id == "seed-1"
    assert result.candidate.industry == "local-service"
    assert (result.candidate.latitude, result.candidate.longitude) == (21.03, 105.83)
    row = db.execute(
        "SELECT source_url, source_type, discovered_at, conflict, snapshot_json "
        "FROM candidate_sources"
    ).fetchone()
    snapshot = json.loads(row["snapshot_json"])
    assert tuple(row)[:4] == (
        "https://maps.google.com/?cid=place-1",
        "google-places",
        "2026-08-12T01:02:03.000000Z",
        0,
    )
    assert snapshot["candidate_seed"] == seed.model_dump(mode="json")
    assert snapshot["cohort"] == "local-service"


def test_discovery_seed_upsert_returns_duplicate_or_conflict_without_losing_evidence(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    first = _seed("https://example.com", "Example", "Hà Nội")
    duplicate = first.validated_replace(
        source_url="https://second.example/source",
        source_type="serper",
        external_id="result-2",
    )
    conflict = duplicate.validated_replace(
        source_url="https://third.example/source",
        business_name="Different business",
        address="Đà Nẵng",
    )

    assert (
        repo.upsert_discovery_seed(first, "other").disposition is DiscoverySeedDisposition.INSERTED
    )
    assert (
        repo.upsert_discovery_seed(duplicate, "other").disposition
        is DiscoverySeedDisposition.DUPLICATE
    )
    assert (
        repo.upsert_discovery_seed(conflict, "other").disposition
        is DiscoverySeedDisposition.CONFLICT
    )
    rows = db.execute(
        "SELECT source_url, conflict, snapshot_json FROM candidate_sources ORDER BY source_url"
    ).fetchall()
    assert len(rows) == 3
    assert sum(int(row["conflict"]) for row in rows) == 1
    assert {json.loads(row["snapshot_json"])["candidate_seed"]["external_id"] for row in rows} == {
        None,
        "result-2",
    }


def test_exact_discovery_seed_retry_is_duplicate_without_duplicate_observation(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    seed = _seed("https://example.com", "Example", "Hà Nội").validated_replace(
        source_url="https://directory.example/evidence/1",
        source_type="serper",
        external_id="result-1",
        metadata={"provider": "serper", "rank": 1},
    )

    first = repo.upsert_discovery_seed(seed, "other")
    retry = repo.upsert_discovery_seed(seed, "other")

    assert first.disposition is DiscoverySeedDisposition.INSERTED
    assert retry.disposition is DiscoverySeedDisposition.DUPLICATE
    rows = db.execute(
        "SELECT source_url, source_type, conflict, snapshot_json FROM candidate_sources"
    ).fetchall()
    assert len(rows) == 1
    assert tuple(rows[0])[:3] == (
        "https://directory.example/evidence/1",
        "serper",
        0,
    )
    assert json.loads(rows[0]["snapshot_json"])["candidate_seed"] == seed.model_dump(mode="json")


def test_discovery_seed_evidence_set_preserves_distinct_sources_but_dedupes_retries(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    first = _seed("https://example.com", "Example", "Hà Nội").validated_replace(
        source_url="https://directory.example/evidence/1", source_type="serper"
    )
    second = first.validated_replace(
        source_url="https://maps.google.com/?cid=place-1",
        source_type="google-places",
        external_id="place-1",
    )

    for seed in (first, first, second, second):
        repo.upsert_discovery_seed(seed, "other")

    rows = db.execute(
        "SELECT source_url, source_type FROM candidate_sources ORDER BY source_type"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("https://maps.google.com/?cid=place-1", "google-places"),
        ("https://directory.example/evidence/1", "serper"),
    ]


def test_concurrent_discovery_seed_upsert_reports_exactly_one_insert(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    db = connect(path)
    migrate(db)
    db.close()
    barrier = Barrier(2)

    def upsert(source_url: str) -> DiscoverySeedDisposition:
        connection = connect(path)
        try:
            barrier.wait(timeout=5)
            seed = _seed("https://example.com", "Example", "Hà Nội").validated_replace(
                source_url=source_url
            )
            return Repository(connection).upsert_discovery_seed(seed, "other").disposition
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        dispositions = list(
            executor.map(
                upsert,
                ["https://one.example/source", "https://two.example/source"],
            )
        )

    check = connect(path)
    assert sorted(disposition.value for disposition in dispositions) == ["duplicate", "inserted"]
    assert Repository(check).count_candidates() == 1
    assert check.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0] == 2


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
    if table in {"evidence", "issues"}:
        _insert_candidate_parent(db)
    if table == "feedback":
        _insert_project_parent(db)
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


def test_score_evidence_ids_are_a_sorted_unique_reference_set(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)

    repo.append_score(_score(evidence_ids=("evidence-b", "evidence-a")))
    repo.append_score(_score(evidence_ids=("evidence-a", "evidence-b")))
    repo.append_score(
        _score(evidence_ids=("evidence-b", "evidence-a", "evidence-b"))
    )

    rows = db.execute("SELECT record_id, snapshot_json FROM scores").fetchall()
    assert len(rows) == 1
    assert len(rows[0]["record_id"]) == 64
    persisted = ScoreRecord.model_validate_json(rows[0]["snapshot_json"])
    assert persisted.evidence_ids == ("evidence-a", "evidence-b")


def test_delivery_enqueue_is_idempotent_by_unique_key_and_returns_persisted_record(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    _insert_project_parent(db)
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


def test_enqueue_delivery_once_reports_single_winner_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    db = connect(path)
    migrate(db)
    _insert_project_parent(db)
    db.close()
    barrier = Barrier(2)

    def enqueue() -> bool:
        connection = open_connection(path)
        try:
            barrier.wait()
            return Repository(connection).enqueue_delivery_once(_delivery())
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: enqueue(), range(2)))

    assert sorted(results) == [False, True]


def test_ensure_review_project_is_atomic_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    db = connect(path)
    migrate(db)
    _insert_candidate_parent(db)
    db.close()
    project = SimpleNamespace(
        project_id="project-production",
        candidate_id="candidate-1",
        market_id="hanoi-80km",
        artifact_dir=str((tmp_path / "artifacts" / "project-production").resolve()),
        created_at=datetime(2026, 8, 13, tzinfo=UTC),
    )
    barrier = Barrier(2)

    def ensure() -> bool:
        connection = open_connection(path)
        try:
            barrier.wait()
            return Repository(connection).ensure_review_project(project)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: ensure(), range(2)))

    assert sorted(results) == [False, True]


def test_delivery_enqueue_tolerates_mutable_state_for_same_immutable_identity(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    _insert_project_parent(db)
    original = repo.enqueue_delivery(_delivery())
    sending = repo.transition_delivery(
        original.delivery_id,
        original,
        original.validated_replace(status=DeliveryState.SENDING),
    )

    replay = repo.enqueue_delivery(_delivery())

    assert replay == sending
    assert replay.status is DeliveryState.SENDING
    assert db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1


def test_delivery_enqueue_rejects_changed_immutable_content_after_state_mutates(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    _insert_project_parent(db)
    original = repo.enqueue_delivery(_delivery())
    repo.transition_delivery(
        original.delivery_id,
        original,
        original.validated_replace(status=DeliveryState.SENDING),
    )

    with pytest.raises(RepositoryConflict, match="immutable content"):
        repo.enqueue_delivery(_delivery().validated_replace(payload_path="other/review.json"))


def test_claim_next_delivery_skips_exhausted_failed_row(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    _insert_project_parent(db)
    first = repo.enqueue_delivery(_delivery())
    exhausted = first.validated_replace(
        status=DeliveryState.FAILED,
        attempt_count=2,
        last_error="exhausted",
    )
    repo.transition_delivery(first.delivery_id, first, exhausted)
    second = _delivery(delivery_id="delivery-2").validated_replace(
        idempotency_key="review-card:project-1:v2"
    )
    repo.enqueue_delivery(second)

    claimed = repo.claim_next_delivery(max_attempts=2)

    assert claimed is not None
    assert claimed.delivery_id == second.delivery_id
    assert claimed.status is DeliveryState.SENDING
    assert repo.get_delivery(first.delivery_id) == exhausted


def test_claim_next_delivery_is_atomic_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    db = connect(path)
    migrate(db)
    repo = Repository(db)
    _insert_project_parent(db)
    repo.enqueue_delivery(_delivery())
    db.close()
    barrier = Barrier(2)

    def claim() -> DeliveryRecord | None:
        connection = open_connection(path)
        try:
            barrier.wait()
            return Repository(connection).claim_next_delivery(max_attempts=2)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: claim(), range(2)))

    assert sum(result is not None for result in results) == 1
    claimed = next(result for result in results if result is not None)
    assert claimed.status is DeliveryState.SENDING


def test_delivery_transition_uses_expected_state_compare_and_swap(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    _insert_project_parent(db)
    pending = repo.enqueue_delivery(_delivery())
    sending = pending.validated_replace(status=DeliveryState.SENDING)

    assert repo.transition_delivery(pending.delivery_id, pending, sending) == sending
    with pytest.raises(RepositoryConflict, match="state changed"):
        repo.transition_delivery(
            pending.delivery_id,
            pending,
            pending.validated_replace(
                status=DeliveryState.FAILED,
                attempt_count=1,
                last_error="late worker",
            ),
        )
    assert repo.get_delivery(pending.delivery_id) == sending


def test_delivery_transition_requires_expected_snapshot_not_only_state(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    _insert_project_parent(db)
    pending = repo.enqueue_delivery(_delivery())
    claimed = repo.claim_next_delivery(max_attempts=2)
    assert claimed is not None
    first_failure = repo.transition_delivery(
        claimed.delivery_id,
        claimed,
        claimed.validated_replace(
            status=DeliveryState.FAILED,
            attempt_count=1,
            last_error="first failure",
        ),
    )
    retry = repo.claim_next_delivery(max_attempts=2)
    assert retry is not None and retry.status is DeliveryState.SENDING

    with pytest.raises(RepositoryConflict, match="snapshot changed"):
        repo.transition_delivery(
            retry.delivery_id,
            claimed,
            claimed.validated_replace(
                status=DeliveryState.FAILED,
                attempt_count=1,
                last_error="late first worker",
            ),
        )
    assert repo.get_delivery(pending.delivery_id) == retry
    assert first_failure.attempt_count == 1


def test_production_project_and_delivery_are_durable_and_idempotent(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    _insert_candidate_parent(db)
    project = SimpleNamespace(
        project_id="project-production",
        candidate_id="candidate-1",
        market_id="hanoi-80km",
        artifact_dir=str((tmp_path / "artifacts" / "project-production").resolve()),
        created_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert repo.ensure_review_project(project)
    assert not repo.ensure_review_project(project)
    delivery = _delivery(project_id=project.project_id).validated_replace(
        delivery_id="delivery-production",
        idempotency_key="review-card:project-production:v1",
    )
    assert repo.enqueue_delivery_once(delivery)
    assert not repo.enqueue_delivery_once(delivery)
    assert repo.get_delivery_status(delivery.idempotency_key) is DeliveryState.PENDING

    project_row = db.execute(
        "SELECT state, state_version, snapshot_json FROM projects WHERE project_id = ?",
        (project.project_id,),
    ).fetchone()
    assert project_row["state"] == ProjectState.REVIEW.value
    assert project_row["state_version"] == 0
    assert json.loads(project_row["snapshot_json"])["artifact_dir"] == project.artifact_dir


def test_project_state_sync_uses_version_compare_and_swap(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    _insert_project_parent(db)

    assert repo.synchronize_project_state(
        "project-1",
        expected_version=0,
        state=ProjectState.APPROVED,
        state_version=1,
    )
    assert not repo.synchronize_project_state(
        "project-1",
        expected_version=0,
        state=ProjectState.REJECTED,
        state_version=1,
    )
    row = db.execute(
        "SELECT state, state_version, snapshot_json FROM projects WHERE project_id = 'project-1'"
    ).fetchone()
    assert (row["state"], row["state_version"]) == (ProjectState.APPROVED.value, 1)
    assert json.loads(row["snapshot_json"])["state"] == ProjectState.APPROVED.value


def test_review_project_replay_tolerates_coordinator_owned_state_changes(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repo = Repository(db)
    _insert_candidate_parent(db)
    project = SimpleNamespace(
        project_id="project-production",
        candidate_id="candidate-1",
        market_id="hanoi-80km",
        artifact_dir=str((tmp_path / "artifacts" / "project-production").resolve()),
        created_at=datetime(2026, 8, 13, tzinfo=UTC),
    )
    assert repo.ensure_review_project(project)
    assert repo.synchronize_project_state(
        project.project_id,
        expected_version=0,
        state=ProjectState.APPROVED,
        state_version=1,
    )

    assert not repo.ensure_review_project(project)


def _foreign_key_shapes(
    db: sqlite3.Connection, table: str
) -> set[tuple[str, str, str, str, str]]:
    return {
        (
            str(row["from"]),
            str(row["table"]),
            str(row["to"]),
            str(row["on_update"]),
            str(row["on_delete"]),
        )
        for row in db.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    }


def test_foreign_key_relationship_classes_are_declared_exactly(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)

    candidate_relationship = {
        ("candidate_id", "candidates", "candidate_id", "NO ACTION", "RESTRICT")
    }
    for table in ("candidate_sources", "pages", "evidence", "issues", "projects"):
        assert _foreign_key_shapes(db, table) == candidate_relationship
    assert _foreign_key_shapes(db, "audits") == candidate_relationship | {
        ("source_run_id", "runs", "run_id", "NO ACTION", "RESTRICT")
    }

    project_relationship = {
        ("project_id", "projects", "project_id", "NO ACTION", "RESTRICT")
    }
    for table in ("feedback", "deliveries", "component_sets", "worklog_events"):
        assert _foreign_key_shapes(db, table) == project_relationship


@pytest.mark.parametrize(
    ("sql", "values", "seed_candidate", "seed_run"),
    [
        (
            """INSERT INTO candidate_sources (
                observation_id, candidate_id, source_url, canonical_domain, source_type,
                discovered_at, conflict, snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "a" * 64,
                "missing",
                "https://example.com/",
                "example.com",
                "direct",
                "2026-08-12T00:00:00.000000Z",
                0,
                "{}",
            ),
            False,
            False,
        ),
        (
            "INSERT INTO pages VALUES (?, ?, ?, ?, ?)",
            ("page-1", "missing", "https://example.com/", "home", "{}"),
            False,
            False,
        ),
        (
            "INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
            (
                "evidence-1",
                "missing",
                "performance",
                "2026-08-12T00:00:00.000000Z",
                "{}",
            ),
            False,
            False,
        ),
        (
            "INSERT INTO audits VALUES (?, ?, ?, ?, ?)",
            ("audit-1", "missing", "run-1", "pending", "{}"),
            False,
            True,
        ),
        (
            "INSERT INTO audits VALUES (?, ?, ?, ?, ?)",
            ("audit-1", "candidate-1", "missing", "pending", "{}"),
            True,
            False,
        ),
        (
            "INSERT INTO issues VALUES (?, ?, ?, ?)",
            ("issue-1", "missing", "P1", "{}"),
            False,
            False,
        ),
        (
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            ("project-1", "missing", "new", 0, "{}"),
            False,
            False,
        ),
        (
            "INSERT INTO feedback VALUES (?, ?, ?, ?, ?)",
            (
                "feedback-1",
                "missing",
                "actor-1",
                "2026-08-12T00:00:00.000000Z",
                "{}",
            ),
            False,
            False,
        ),
        (
            "INSERT INTO deliveries VALUES (?, ?, ?, ?, ?, ?)",
            ("delivery-1", "delivery-key", "missing", "channel-1", "pending", "{}"),
            False,
            False,
        ),
        (
            "INSERT INTO component_sets VALUES (?, ?, ?, ?, ?, ?)",
            (
                "components-1",
                "channel-1",
                "message-1",
                "missing",
                "2026-08-12T00:00:00.000000Z",
                "{}",
            ),
            False,
            False,
        ),
        (
            "INSERT INTO worklog_events VALUES (?, ?, ?, ?, ?)",
            (
                "event-1",
                "missing",
                "created",
                "2026-08-12T00:00:00.000000Z",
                "{}",
            ),
            False,
            False,
        ),
    ],
)
def test_foreign_key_relationships_reject_missing_parents(
    tmp_path: Path,
    sql: str,
    values: tuple[object, ...],
    seed_candidate: bool,
    seed_run: bool,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    if seed_candidate:
        _insert_candidate_parent(db)
    if seed_run:
        _insert_run_parent(db)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute(sql, values)


def test_every_snapshot_column_requires_a_valid_json_object(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    snapshot_tables = {
        "runs",
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
    expected_check = "check(json_valid(snapshot_json)andjson_type(snapshot_json)='object')"

    for table in snapshot_tables:
        sql = str(
            db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()["sql"]
        )
        normalized = "".join(sql.lower().split())
        assert expected_check in normalized

    for invalid_snapshot in ("not-json", "[]"):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute(
                """
                INSERT INTO candidates (
                    candidate_id, canonical_domain, normalized_name, state, snapshot_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"candidate-{invalid_snapshot}",
                    f"{invalid_snapshot}.example",
                    invalid_snapshot,
                    "new",
                    invalid_snapshot,
                ),
            )
