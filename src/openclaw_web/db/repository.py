"""Transactional repositories for canonical workflow records."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Self, TypeVar

import tldextract
from pydantic import AnyHttpUrl, BaseModel, TypeAdapter

from openclaw_web.models import (
    Candidate,
    CandidateSeed,
    DeliveryRecord,
    Evidence,
    FeedbackEvent,
    IssueRecord,
    RunRecord,
    ScoreRecord,
    WebUrl,
)


class RepositoryError(RuntimeError):
    """Base class for persistence-level failures."""


class RepositoryConflict(RepositoryError):
    """An immutable identity already exists with different content."""


RepositoryConflictError = RepositoryConflict


class RunConfigMismatchError(RepositoryConflict):
    """A run idempotency key was resumed with a different configuration."""


_ModelT = TypeVar("_ModelT", bound=BaseModel)
_WEB_URL_ADAPTER = TypeAdapter(WebUrl)
_DOMAIN_EXTRACTOR = tldextract.TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
    fallback_to_snapshot=True,
    include_psl_private_domains=True,
)


def _canonical_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_mapping_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_nonblank(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value.strip()


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _lease_expiry(now: datetime, lease_seconds: int) -> tuple[str, str]:
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be a positive integer")
    normalized_now = now.astimezone(UTC) if now.tzinfo is not None else now
    now_text = _utc_text(normalized_now)
    return now_text, _utc_text(normalized_now + timedelta(seconds=lease_seconds))


def _validated_url(value: str | AnyHttpUrl) -> AnyHttpUrl:
    validated = _WEB_URL_ADAPTER.validate_python(str(value))
    if validated.username is not None or validated.password is not None:
        raise ValueError("website URL must not include credentials")
    if validated.host is None:
        raise ValueError("website URL must include a host")
    return validated


def _canonical_domain(value: str | AnyHttpUrl) -> tuple[AnyHttpUrl, str]:
    url = _validated_url(value)
    raw_host = url.host
    if raw_host is None:
        raise ValueError("website URL must include a host")
    host = raw_host.lower().rstrip(".").removeprefix("www.")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        raise ValueError("website URL must include a host")

    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        extracted = _DOMAIN_EXTRACTOR(host)
        registrable = extracted.top_domain_under_public_suffix
        if registrable:
            host = registrable.lower().rstrip(".")
    else:
        host = f"[{parsed_ip.compressed}]" if parsed_ip.version == 6 else parsed_ip.compressed

    default_port = 80 if url.scheme == "http" else 443
    if url.port is not None and url.port != default_port:
        if ":" in host and not host.startswith("["):
            host = f"[{host}]:{url.port}"
        else:
            host = f"{host}:{url.port}"
    return url, host


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    without_punctuation = "".join(
        " " if unicodedata.category(character).startswith(("P", "Z")) else character
        for character in normalized
    )
    return re.sub(r"\s+", " ", without_punctuation).strip()


@contextmanager
def _immediate_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise RepositoryError("repository mutation cannot run inside another transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    else:
        connection.commit()


def _deserialize(model_type: type[_ModelT], snapshot: str) -> _ModelT:
    return model_type.model_validate_json(snapshot)


class Repository:
    """Explicit-transaction persistence for immutable workflow records."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def close(self) -> None:
        """Close the owned SQLite connection."""

        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _candidate_from_row(self, row: sqlite3.Row) -> Candidate:
        return _deserialize(Candidate, str(row["snapshot_json"]))

    def _append_candidate_source(
        self,
        candidate: Candidate,
        source_url: AnyHttpUrl,
        canonical_domain: str,
        discovered_at: datetime,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO candidate_sources (
                candidate_id, source_url, canonical_domain, source_type, discovered_at,
                snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, source_url) DO NOTHING
            """,
            (
                candidate.candidate_id,
                str(source_url),
                canonical_domain,
                "direct",
                _utc_text(discovered_at),
                _canonical_json(candidate),
            ),
        )

    def upsert_candidate(
        self,
        url: str,
        business_name: str,
        address: str | None,
    ) -> Candidate:
        validated_url, canonical_domain = _canonical_domain(url)
        normalized_name = _normalize_match_text(_require_nonblank(business_name, "business_name"))
        normalized_address = (
            _normalize_match_text(_require_nonblank(address, "address"))
            if address is not None
            else None
        )
        now = datetime.now(UTC)
        candidate = Candidate(
            candidate_id=f"candidate-{uuid.uuid5(uuid.NAMESPACE_URL, canonical_domain).hex}",
            name=business_name,
            website_url=validated_url,
            canonical_domain=canonical_domain,
            address=address,
            discovered_at=now,
            updated_at=now,
            source_urls=[validated_url],
        )
        snapshot = _canonical_json(candidate)

        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT snapshot_json FROM candidates WHERE canonical_domain = ?",
                (canonical_domain,),
            ).fetchone()
            if row is None and normalized_address is not None:
                row = self.connection.execute(
                    """
                    SELECT snapshot_json FROM candidates
                    WHERE normalized_name = ? AND normalized_address = ?
                    """,
                    (normalized_name, normalized_address),
                ).fetchone()
            if row is not None:
                persisted = self._candidate_from_row(row)
                self._append_candidate_source(persisted, validated_url, canonical_domain, now)
                return persisted

            self.connection.execute(
                """
                INSERT INTO candidates (
                    candidate_id, canonical_domain, normalized_name, normalized_address,
                    state, discovered_at, updated_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    canonical_domain,
                    normalized_name,
                    normalized_address,
                    candidate.state.value,
                    _utc_text(now),
                    _utc_text(now),
                    snapshot,
                ),
            )
            self._append_candidate_source(candidate, validated_url, canonical_domain, now)
        return candidate

    def find_duplicate(self, candidate: CandidateSeed) -> Candidate | None:
        _, canonical_domain = _canonical_domain(candidate.url)
        row = self.connection.execute(
            """
            SELECT c.snapshot_json
            FROM candidates AS c
            WHERE c.canonical_domain = ?
            UNION ALL
            SELECT c.snapshot_json
            FROM candidate_sources AS s
            JOIN candidates AS c ON c.candidate_id = s.candidate_id
            WHERE s.canonical_domain = ?
            LIMIT 1
            """,
            (canonical_domain, canonical_domain),
        ).fetchone()
        if row is None and candidate.address is not None:
            row = self.connection.execute(
                """
                SELECT snapshot_json FROM candidates
                WHERE normalized_name = ? AND normalized_address = ?
                LIMIT 1
                """,
                (
                    _normalize_match_text(candidate.business_name),
                    _normalize_match_text(candidate.address),
                ),
            ).fetchone()
        return None if row is None else self._candidate_from_row(row)

    def create_or_resume_run(self, idempotency_key: str, config_version: str) -> RunRecord:
        key = _require_nonblank(idempotency_key, "idempotency_key")
        version = _require_nonblank(config_version, "config_version")
        run = RunRecord(
            run_id=f"run-{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}",
            status="pending",
            started_at=datetime.now(UTC),
            idempotency_key=key,
            config_version=version,
        )
        snapshot = _canonical_json(run)

        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT snapshot_json FROM runs WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is not None:
                persisted = _deserialize(RunRecord, str(row["snapshot_json"]))
                if persisted.config_version != version:
                    raise RunConfigMismatchError(
                        "run idempotency key already exists with a different config version"
                    )
                return persisted
            self.connection.execute(
                """
                INSERT INTO runs (
                    run_id, idempotency_key, config_version, status, started_at,
                    completed_at, current_stage, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    key,
                    version,
                    run.status,
                    _utc_text(run.started_at),
                    None,
                    None,
                    snapshot,
                ),
            )
        return run

    def acquire_run_lock(
        self,
        key: str,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        lock_key = _require_nonblank(key, "key")
        owner_token = _require_nonblank(owner, "owner")
        now_text, expiry_text = _lease_expiry(now, lease_seconds)
        with _immediate_transaction(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO run_locks (lock_key, owner, acquired_at, lease_expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lock_key) DO UPDATE SET
                    owner = excluded.owner,
                    acquired_at = excluded.acquired_at,
                    lease_expires_at = excluded.lease_expires_at
                WHERE run_locks.owner = excluded.owner
                   OR run_locks.lease_expires_at < excluded.acquired_at
                """,
                (lock_key, owner_token, now_text, expiry_text),
            )
            return cursor.rowcount == 1

    def renew_run_lock(
        self,
        key: str,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        lock_key = _require_nonblank(key, "key")
        owner_token = _require_nonblank(owner, "owner")
        now_text, expiry_text = _lease_expiry(now, lease_seconds)
        with _immediate_transaction(self.connection):
            cursor = self.connection.execute(
                """
                UPDATE run_locks
                SET acquired_at = ?, lease_expires_at = ?
                WHERE lock_key = ? AND owner = ? AND lease_expires_at >= ?
                """,
                (now_text, expiry_text, lock_key, owner_token, now_text),
            )
            return cursor.rowcount == 1

    def release_run_lock(self, key: str, owner: str) -> None:
        lock_key = _require_nonblank(key, "key")
        owner_token = _require_nonblank(owner, "owner")
        with _immediate_transaction(self.connection):
            self.connection.execute(
                "DELETE FROM run_locks WHERE lock_key = ? AND owner = ?",
                (lock_key, owner_token),
            )

    def _append_snapshot(
        self,
        *,
        table: str,
        identity_column: str,
        record_id: str,
        model: BaseModel,
        model_type: type[_ModelT],
        insert_sql: str,
        values: tuple[Any, ...],
    ) -> None:
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                f"SELECT snapshot_json FROM {table} WHERE {identity_column} = ?", (record_id,)
            ).fetchone()
            if row is not None:
                persisted = _deserialize(model_type, str(row["snapshot_json"]))
                if persisted != model:
                    raise RepositoryConflict(
                        f"{table} identity already exists with different immutable content"
                    )
                return
            self.connection.execute(insert_sql, values)

    def append_evidence(self, evidence: Evidence) -> None:
        snapshot = _canonical_json(evidence)
        self._append_snapshot(
            table="evidence",
            identity_column="evidence_id",
            record_id=evidence.evidence_id,
            model=evidence,
            model_type=Evidence,
            insert_sql="""
                INSERT INTO evidence (
                    evidence_id, candidate_id, evidence_type, captured_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?)
            """,
            values=(
                evidence.evidence_id,
                evidence.candidate_id,
                evidence.evidence_type,
                _utc_text(evidence.captured_at),
                snapshot,
            ),
        )

    def append_score(self, score: ScoreRecord) -> None:
        snapshot = _canonical_json(score)
        identity_json = _canonical_mapping_json(
            {
                "evidence_ids": list(score.evidence_ids),
                "inputs": score.model_dump(mode="json")["inputs"],
                "rubric_version": score.rubric_version,
                "score_name": score.score_name,
            }
        )
        record_id = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
        self._append_snapshot(
            table="scores",
            identity_column="record_id",
            record_id=record_id,
            model=score,
            model_type=ScoreRecord,
            insert_sql="""
                INSERT INTO scores (record_id, score_name, rubric_version, snapshot_json)
                VALUES (?, ?, ?, ?)
            """,
            values=(record_id, score.score_name, score.rubric_version, snapshot),
        )

    def append_issue(self, issue: IssueRecord) -> None:
        snapshot = _canonical_json(issue)
        self._append_snapshot(
            table="issues",
            identity_column="issue_id",
            record_id=issue.issue_id,
            model=issue,
            model_type=IssueRecord,
            insert_sql="""
                INSERT INTO issues (issue_id, candidate_id, severity, snapshot_json)
                VALUES (?, ?, ?, ?)
            """,
            values=(issue.issue_id, issue.candidate_id, issue.severity.value, snapshot),
        )

    def enqueue_delivery(self, delivery: DeliveryRecord) -> DeliveryRecord:
        snapshot = _canonical_json(delivery)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT snapshot_json FROM deliveries
                WHERE delivery_id = ? OR idempotency_key = ?
                LIMIT 1
                """,
                (delivery.delivery_id, delivery.idempotency_key),
            ).fetchone()
            if row is not None:
                persisted = _deserialize(DeliveryRecord, str(row["snapshot_json"]))
                if persisted != delivery:
                    raise RepositoryConflict(
                        "delivery identity already exists with different immutable content"
                    )
                return persisted
            self.connection.execute(
                """
                INSERT INTO deliveries (
                    delivery_id, idempotency_key, project_id, channel_id, status, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery.delivery_id,
                    delivery.idempotency_key,
                    delivery.project_id,
                    delivery.channel_id,
                    delivery.status.value,
                    snapshot,
                ),
            )
        return delivery

    def append_feedback(self, feedback: FeedbackEvent) -> None:
        snapshot = _canonical_json(feedback)
        self._append_snapshot(
            table="feedback",
            identity_column="event_id",
            record_id=feedback.event_id,
            model=feedback,
            model_type=FeedbackEvent,
            insert_sql="""
                INSERT INTO feedback (
                    event_id, project_id, actor_id, created_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?)
            """,
            values=(
                feedback.event_id,
                feedback.project_id,
                feedback.actor_id,
                _utc_text(feedback.created_at),
                snapshot,
            ),
        )

    def count_candidates(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM candidates").fetchone()
        return int(row["count"])
