"""Versioned SQLite schema migrations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


class MigrationError(RuntimeError):
    """Raised when the database cannot be migrated safely."""


@dataclass(frozen=True)
class Migration:
    """One ordered, transactional schema migration."""

    version: int
    statements: tuple[str, ...]


_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
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
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
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
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
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
            candidate_id TEXT NOT NULL,
            source_url TEXT NOT NULL CHECK (length(source_url) > 0),
            canonical_domain TEXT NOT NULL CHECK (length(canonical_domain) > 0),
            source_type TEXT NOT NULL CHECK (length(source_type) > 0),
            discovered_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1),
            PRIMARY KEY (candidate_id, source_url),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX ix_candidate_sources_domain ON candidate_sources (canonical_domain)",
        """
        CREATE TABLE pages (
            page_id TEXT PRIMARY KEY CHECK (length(page_id) > 0),
            candidate_id TEXT NOT NULL,
            page_url TEXT NOT NULL CHECK (length(page_url) > 0),
            page_type TEXT NOT NULL CHECK (length(page_type) > 0),
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE evidence (
            evidence_id TEXT PRIMARY KEY CHECK (length(evidence_id) > 0),
            candidate_id TEXT NOT NULL CHECK (length(candidate_id) > 0),
            evidence_type TEXT NOT NULL CHECK (length(evidence_type) > 0),
            captured_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
        )
        """,
        "CREATE INDEX ix_evidence_candidate_id ON evidence (candidate_id)",
        """
        CREATE TABLE audits (
            audit_id TEXT PRIMARY KEY CHECK (length(audit_id) > 0),
            candidate_id TEXT NOT NULL CHECK (length(candidate_id) > 0),
            source_run_id TEXT NOT NULL CHECK (length(source_run_id) > 0),
            status TEXT NOT NULL CHECK (length(status) > 0),
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
        )
        """,
        """
        CREATE TABLE scores (
            record_id TEXT PRIMARY KEY CHECK (length(record_id) = 64),
            score_name TEXT NOT NULL CHECK (length(score_name) > 0),
            rubric_version TEXT NOT NULL CHECK (length(rubric_version) > 0),
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
        )
        """,
        """
        CREATE TABLE issues (
            issue_id TEXT PRIMARY KEY CHECK (length(issue_id) > 0),
            candidate_id TEXT NOT NULL CHECK (length(candidate_id) > 0),
            severity TEXT NOT NULL CHECK (severity IN ('P0', 'P1', 'P2', 'P3')),
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
        )
        """,
        "CREATE INDEX ix_issues_candidate_id ON issues (candidate_id)",
        """
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY CHECK (length(project_id) > 0),
            candidate_id TEXT,
            state TEXT NOT NULL CHECK (length(state) > 0),
            state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1),
            FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE SET NULL
        )
        """,
        """
        CREATE TABLE feedback (
            event_id TEXT PRIMARY KEY CHECK (length(event_id) > 0),
            project_id TEXT NOT NULL CHECK (length(project_id) > 0),
            actor_id TEXT NOT NULL CHECK (length(actor_id) > 0),
            created_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
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
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
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
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
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
            snapshot_json TEXT NOT NULL CHECK (length(snapshot_json) > 1)
        )
        """,
        "CREATE INDEX ix_worklog_events_project_id ON worklog_events (project_id)",
    ),
)

_MIGRATIONS = (_MIGRATION_1,)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def migrate(connection: sqlite3.Connection) -> None:
    """Apply every unapplied schema version in one explicit transaction."""

    if connection.in_transaction:
        raise MigrationError("cannot migrate while a transaction is active")

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(_SCHEMA_MIGRATIONS_SQL)
        applied = {
            int(row[0])
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        known = {migration.version for migration in _MIGRATIONS}
        unknown = applied - known
        if unknown:
            versions = ", ".join(str(version) for version in sorted(unknown))
            raise MigrationError(f"database contains unknown migration versions: {versions}")

        for migration in _MIGRATIONS:
            if migration.version in applied:
                continue
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (migration.version, _utc_text(datetime.now(UTC))),
            )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    else:
        connection.commit()
