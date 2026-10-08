"""Versioned SQLite schema migrations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise


class MigrationError(RuntimeError):
    """Raised when the database cannot be migrated safely."""


@dataclass(frozen=True)
class Migration:
    """One ordered, transactional schema migration."""

    version: int
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        """Return a stable digest over the immutable versioned SQL definition."""

        payload = json.dumps(
            {"statements": self.statements, "version": self.version},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    checksum TEXT NOT NULL CHECK (length(checksum) = 64),
    applied_at TEXT NOT NULL
)
"""


_MIGRATION_1 = Migration(
    version=1,
    statements=(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY CHECK (length(run_id) > 0),
            idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
            config_version TEXT NOT NULL CHECK (length(config_version) > 0),
            status TEXT NOT NULL CHECK (length(status) > 0),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            current_stage TEXT,
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object')
        )
        """,
        "CREATE UNIQUE INDEX ux_runs_idempotency_key ON runs (idempotency_key)",
        """
        CREATE TABLE run_locks (
            lock_key TEXT PRIMARY KEY CHECK (length(lock_key) > 0),
            owner TEXT NOT NULL CHECK (length(owner) > 0),
            acquired_at TEXT NOT NULL,
            lease_expires_at TEXT NOT NULL,
            CHECK (lease_expires_at >= acquired_at)
        )
        """,
        """
        CREATE TABLE candidates (
            candidate_id TEXT PRIMARY KEY CHECK (length(candidate_id) > 0),
            canonical_domain TEXT NOT NULL CHECK (length(canonical_domain) > 0),
            normalized_name TEXT NOT NULL CHECK (length(normalized_name) > 0),
            normalized_address TEXT,
            state TEXT NOT NULL CHECK (length(state) > 0),
            discovered_at TEXT,
            updated_at TEXT,
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object')
        )
        """,
        "CREATE UNIQUE INDEX ux_candidates_canonical_domain ON candidates (canonical_domain)",
        """
        CREATE UNIQUE INDEX ux_candidates_name_address
        ON candidates (normalized_name, normalized_address)
        WHERE normalized_address IS NOT NULL
        """,
        """
        CREATE TABLE candidate_sources (
            observation_id TEXT PRIMARY KEY CHECK (length(observation_id) = 64),
            candidate_id TEXT NOT NULL,
            source_url TEXT NOT NULL CHECK (length(source_url) > 0),
            canonical_domain TEXT NOT NULL CHECK (length(canonical_domain) > 0),
            source_type TEXT NOT NULL CHECK (length(source_type) > 0),
            discovered_at TEXT NOT NULL,
            conflict INTEGER NOT NULL DEFAULT 0 CHECK (conflict IN (0, 1)),
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX ix_candidate_sources_domain ON candidate_sources (canonical_domain)",
        """
        CREATE TABLE pages (
            page_id TEXT PRIMARY KEY CHECK (length(page_id) > 0),
            candidate_id TEXT NOT NULL,
            page_url TEXT NOT NULL CHECK (length(page_url) > 0),
            page_type TEXT NOT NULL CHECK (length(page_type) > 0),
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE evidence (
            evidence_id TEXT PRIMARY KEY CHECK (length(evidence_id) > 0),
            candidate_id TEXT NOT NULL CHECK (length(candidate_id) > 0),
            evidence_type TEXT NOT NULL CHECK (length(evidence_type) > 0),
            captured_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX ix_evidence_candidate_id ON evidence (candidate_id)",
        """
        CREATE TABLE audits (
            audit_id TEXT PRIMARY KEY CHECK (length(audit_id) > 0),
            candidate_id TEXT NOT NULL CHECK (length(candidate_id) > 0),
            source_run_id TEXT NOT NULL CHECK (length(source_run_id) > 0),
            status TEXT NOT NULL CHECK (length(status) > 0),
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE RESTRICT,
            FOREIGN KEY (source_run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE scores (
            record_id TEXT PRIMARY KEY CHECK (length(record_id) = 64),
            score_name TEXT NOT NULL CHECK (length(score_name) > 0),
            rubric_version TEXT NOT NULL CHECK (length(rubric_version) > 0),
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object')
        )
        """,
        """
        CREATE TABLE issues (
            issue_id TEXT PRIMARY KEY CHECK (length(issue_id) > 0),
            candidate_id TEXT NOT NULL CHECK (length(candidate_id) > 0),
            severity TEXT NOT NULL CHECK (severity IN ('P0', 'P1', 'P2', 'P3')),
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX ix_issues_candidate_id ON issues (candidate_id)",
        """
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY CHECK (length(project_id) > 0),
            candidate_id TEXT,
            state TEXT NOT NULL CHECK (length(state) > 0),
            state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE feedback (
            event_id TEXT PRIMARY KEY CHECK (length(event_id) > 0),
            project_id TEXT NOT NULL CHECK (length(project_id) > 0),
            actor_id TEXT NOT NULL CHECK (length(actor_id) > 0),
            created_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX ix_feedback_project_id ON feedback (project_id)",
        """
        CREATE TABLE deliveries (
            delivery_id TEXT PRIMARY KEY CHECK (length(delivery_id) > 0),
            idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
            project_id TEXT NOT NULL CHECK (length(project_id) > 0),
            channel_id TEXT NOT NULL CHECK (length(channel_id) > 0),
            status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """,
        "CREATE UNIQUE INDEX ux_deliveries_idempotency_key ON deliveries (idempotency_key)",
        """
        CREATE TABLE component_sets (
            component_set_id TEXT PRIMARY KEY CHECK (length(component_set_id) > 0),
            channel_id TEXT NOT NULL CHECK (length(channel_id) > 0),
            message_id TEXT NOT NULL CHECK (length(message_id) > 0),
            project_id TEXT NOT NULL CHECK (length(project_id) > 0),
            expires_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE UNIQUE INDEX ux_component_sets_bot_message
        ON component_sets (channel_id, message_id)
        """,
        """
        CREATE TABLE worklog_events (
            event_id TEXT PRIMARY KEY CHECK (length(event_id) > 0),
            project_id TEXT NOT NULL CHECK (length(project_id) > 0),
            event_type TEXT NOT NULL CHECK (length(event_type) > 0),
            created_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
        )
        """,
        "CREATE INDEX ix_worklog_events_project_id ON worklog_events (project_id)",
    ),
)

_MIGRATION_2 = Migration(
    version=2,
    statements=(
        """
        CREATE TABLE component_actions (
            component_set_id TEXT NOT NULL,
            actor_id TEXT NOT NULL CHECK (length(actor_id) > 0),
            action TEXT NOT NULL CHECK (length(action) > 0),
            claimed_at TEXT NOT NULL,
            PRIMARY KEY (component_set_id, actor_id, action),
            FOREIGN KEY (component_set_id)
                REFERENCES component_sets(component_set_id) ON DELETE RESTRICT
        )
        """,
    ),
)

_MIGRATION_3 = Migration(
    version=3,
    statements=(
        "ALTER TABLE component_actions ADD COLUMN confirmed_state TEXT",
        "ALTER TABLE component_actions ADD COLUMN confirmed_state_version INTEGER",
        """
        CREATE TRIGGER component_actions_confirmation_shape_insert
        BEFORE INSERT ON component_actions
        WHEN (NEW.confirmed_state IS NULL) != (NEW.confirmed_state_version IS NULL)
          OR (NEW.confirmed_state_version IS NOT NULL AND NEW.confirmed_state_version < 0)
        BEGIN
            SELECT RAISE(ABORT, 'invalid component action confirmation');
        END
        """,
        """
        CREATE TRIGGER component_actions_confirmation_shape_update
        BEFORE UPDATE OF confirmed_state, confirmed_state_version ON component_actions
        WHEN (NEW.confirmed_state IS NULL) != (NEW.confirmed_state_version IS NULL)
          OR (NEW.confirmed_state_version IS NOT NULL AND NEW.confirmed_state_version < 0)
        BEGIN
            SELECT RAISE(ABORT, 'invalid component action confirmation');
        END
        """,
    ),
)

_MIGRATION_4 = Migration(
    version=4,
    statements=(
        """
        CREATE TABLE portfolios (
            portfolio_id TEXT PRIMARY KEY CHECK (length(portfolio_id) > 0),
            run_id TEXT,
            status TEXT NOT NULL CHECK (length(status) > 0),
            target INTEGER NOT NULL CHECK (target BETWEEN 3 AND 7),
            maximum INTEGER NOT NULL CHECK (maximum BETWEEN target AND 7),
            state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
        )
        """,
        "CREATE UNIQUE INDEX ux_portfolios_run_id ON portfolios (run_id) WHERE run_id IS NOT NULL",
        """
        CREATE TABLE portfolio_entries (
            entry_id TEXT PRIMARY KEY CHECK (length(entry_id) > 0),
            portfolio_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL CHECK (length(candidate_id) > 0),
            rank INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 7),
            state TEXT NOT NULL CHECK (length(state) > 0),
            state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id) ON DELETE RESTRICT,
            UNIQUE (portfolio_id, candidate_id)
        )
        """,
        "CREATE INDEX ix_portfolio_entries_portfolio_id ON portfolio_entries (portfolio_id, rank)",
        """
        CREATE TABLE portfolio_deliveries (
            portfolio_id TEXT PRIMARY KEY,
            delivery_id TEXT NOT NULL CHECK (length(delivery_id) > 0),
            idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
            status TEXT NOT NULL CHECK (length(status) > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
                CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
            FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id) ON DELETE RESTRICT,
            UNIQUE (delivery_id),
            UNIQUE (idempotency_key)
        )
        """,
        """
        CREATE TABLE dashboard_action_receipts (
            idempotency_key TEXT PRIMARY KEY CHECK (length(idempotency_key) > 0),
            action TEXT NOT NULL CHECK (length(action) > 0),
            target_id TEXT NOT NULL CHECK (length(target_id) > 0),
            actor_id TEXT NOT NULL CHECK (length(actor_id) > 0),
            expected_state_version INTEGER NOT NULL CHECK (expected_state_version >= 0),
            status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
            event_id TEXT,
            response_json TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (response_json IS NULL OR (json_valid(response_json) AND json_type(response_json) = 'object'))
        )
        """,
        "CREATE INDEX ix_dashboard_action_receipts_target ON dashboard_action_receipts (target_id, created_at)",
    ),
)

_MIGRATIONS = (_MIGRATION_1, _MIGRATION_2, _MIGRATION_3, _MIGRATION_4)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_definitions(migrations: tuple[Migration, ...]) -> None:
    if not isinstance(migrations, tuple) or not migrations or any(
        not isinstance(migration, Migration)
        or not isinstance(migration.statements, tuple)
        or not migration.statements
        or any(
            not isinstance(statement, str) or not statement.strip()
            for statement in migration.statements
        )
        for migration in migrations
    ):
        raise MigrationError(
            "migration definitions must contain immutable non-empty SQL statement tuples"
        )

    versions = [migration.version for migration in migrations]
    valid_versions = all(
        isinstance(version, int) and not isinstance(version, bool) and version > 0
        for version in versions
    )
    if not valid_versions or any(current <= previous for previous, current in pairwise(versions)):
        raise MigrationError(
            "migration definitions must use unique positive strictly increasing versions"
        )


def _read_and_verify_applied(
    connection: sqlite3.Connection, migrations: tuple[Migration, ...]
) -> tuple[int, ...]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return ()

    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(schema_migrations)")
    }
    required_columns = {"version", "checksum", "applied_at"}
    if columns != required_columns:
        raise MigrationError(
            "schema_migrations has an incompatible history contract; refusing repair"
        )

    rows = connection.execute(
        "SELECT version, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    applied_versions = tuple(int(row[0]) for row in rows)
    expected_prefix = tuple(
        migration.version for migration in migrations[: len(applied_versions)]
    )
    if applied_versions != expected_prefix:
        raise MigrationError(
            "applied migration versions must form an exact prefix of known definitions"
        )

    by_version = {migration.version: migration for migration in migrations}
    for row in rows:
        version = int(row[0])
        checksum = str(row[1])
        if checksum != by_version[version].checksum:
            raise MigrationError(f"migration {version} checksum does not match its definition")
    return applied_versions


def migrate(connection: sqlite3.Connection) -> None:
    """Apply every unapplied schema version in one explicit transaction."""

    if connection.in_transaction:
        raise MigrationError("cannot migrate while a transaction is active")
    migrations = _MIGRATIONS
    _validate_definitions(migrations)

    connection.execute("BEGIN IMMEDIATE")
    try:
        applied_versions = _read_and_verify_applied(connection, migrations)
        connection.execute(_SCHEMA_MIGRATIONS_SQL)
        for migration in migrations[len(applied_versions) :]:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, checksum, applied_at)
                VALUES (?, ?, ?)
                """,
                (migration.version, migration.checksum, _utc_text(datetime.now(UTC))),
            )
        connection.commit()
    except BaseException as error:
        try:
            connection.rollback()
        except BaseException as rollback_error:  # noqa: BLE001 - keep the primary failure
            error.add_note(f"rollback also failed: {rollback_error!r}")
        raise
