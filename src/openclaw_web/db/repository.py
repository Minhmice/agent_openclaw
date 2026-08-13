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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, Self, TypeVar

import tldextract
from pydantic import AnyHttpUrl, BaseModel, TypeAdapter

from openclaw_web.models import (
    Candidate,
    CandidateSeed,
    ComponentSet,
    DeliveryRecord,
    DeliveryState,
    Evidence,
    FeedbackEvent,
    IssueRecord,
    ProjectState,
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


class DiscoverySeedDisposition(StrEnum):
    """Atomic result of persisting one discovery observation."""

    INSERTED = "inserted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class DiscoverySeedBatch:
    """One representative candidate plus its intact same-domain observations."""

    seed: CandidateSeed
    observations: tuple[CandidateSeed, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.seed, CandidateSeed):
            raise TypeError("seed must be a CandidateSeed")
        if not self.observations:
            raise ValueError("observations must not be empty")
        if any(not isinstance(item, CandidateSeed) for item in self.observations):
            raise TypeError("observations must contain CandidateSeed values")


@dataclass(frozen=True, slots=True)
class DiscoverySeedUpsertResult:
    disposition: DiscoverySeedDisposition
    candidate: Candidate


class ReviewProjectRecord(Protocol):
    """Structural input accepted from the production pipeline without an import cycle."""

    project_id: str
    candidate_id: str
    market_id: str
    artifact_dir: str
    created_at: datetime


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
        connection.commit()
    except BaseException as error:
        try:
            connection.rollback()
        except BaseException as rollback_error:  # noqa: BLE001 - keep the primary failure
            error.add_note(f"rollback also failed: {rollback_error!r}")
        raise


def _deserialize(model_type: type[_ModelT], snapshot: str) -> _ModelT:
    return model_type.model_validate_json(snapshot)


def _delivery_immutable_identity(delivery: DeliveryRecord) -> tuple[str, ...]:
    return (
        delivery.delivery_id,
        delivery.event_type,
        delivery.project_id,
        delivery.channel_id,
        delivery.payload_path,
        delivery.idempotency_key,
    )


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
        candidate_id: str,
        source_url: AnyHttpUrl,
        canonical_domain: str,
        business_name: str,
        address: str | None,
        normalized_name: str,
        normalized_address: str | None,
        discovered_at: datetime,
        *,
        conflict: bool,
    ) -> None:
        identity = {
            "address": address,
            "business_name": business_name,
            "candidate_id": candidate_id,
            "canonical_domain": canonical_domain,
            "normalized_address": normalized_address,
            "normalized_name": normalized_name,
            "source_type": "direct",
            "source_url": str(source_url),
        }
        observation_id = hashlib.sha256(
            _canonical_mapping_json(identity).encode("utf-8")
        ).hexdigest()
        snapshot = _canonical_mapping_json(
            {**identity, "discovered_at": _utc_text(discovered_at)}
        )
        self.connection.execute(
            """
            INSERT INTO candidate_sources (
                observation_id, candidate_id, source_url, canonical_domain, source_type,
                discovered_at, conflict, snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_id) DO NOTHING
            """,
            (
                observation_id,
                candidate_id,
                str(source_url),
                canonical_domain,
                "direct",
                _utc_text(discovered_at),
                int(conflict),
                snapshot,
            ),
        )

    def _append_discovery_seed_source(
        self,
        candidate_id: str,
        canonical_domain: str,
        seed: CandidateSeed,
        cohort: str,
        *,
        conflict: bool,
    ) -> None:
        seed_payload = seed.model_dump(mode="json")
        identity = {
            "candidate_id": candidate_id,
            "candidate_seed": seed_payload,
            "canonical_domain": canonical_domain,
            "cohort": cohort,
        }
        observation_id = hashlib.sha256(
            _canonical_mapping_json(identity).encode("utf-8")
        ).hexdigest()
        snapshot = _canonical_mapping_json(
            {
                "candidate_id": candidate_id,
                "candidate_seed": seed_payload,
                "canonical_domain": canonical_domain,
                "cohort": cohort,
            }
        )
        self.connection.execute(
            """
            INSERT INTO candidate_sources (
                observation_id, candidate_id, source_url, canonical_domain, source_type,
                discovered_at, conflict, snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_id) DO NOTHING
            """,
            (
                observation_id,
                candidate_id,
                str(seed.source_url),
                canonical_domain,
                seed.source_type,
                _utc_text(seed.discovered_at),
                int(conflict),
                snapshot,
            ),
        )

    def upsert_discovery_seed(
        self,
        seed: CandidateSeed | DiscoverySeedBatch,
        cohort: str,
    ) -> DiscoverySeedUpsertResult:
        """Atomically persist a representative and every intact source observation."""

        if isinstance(seed, DiscoverySeedBatch):
            representative = seed.seed
            observations = seed.observations
        elif isinstance(seed, CandidateSeed):
            representative = seed
            observations = (seed,)
        else:
            raise TypeError("seed must be a CandidateSeed or DiscoverySeedBatch")
        normalized_cohort = _require_nonblank(cohort, "cohort")
        validated_url, canonical_domain = _canonical_domain(representative.url)
        if any(_canonical_domain(item.url)[1] != canonical_domain for item in observations):
            raise ValueError("all discovery observations must share a canonical domain")
        normalized_name = _normalize_match_text(representative.business_name)
        normalized_address = (
            _normalize_match_text(representative.address)
            if representative.address is not None
            else None
        )
        candidate = Candidate(
            candidate_id=f"candidate-{uuid.uuid5(uuid.NAMESPACE_URL, canonical_domain).hex}",
            name=representative.business_name,
            seed_id=representative.seed_id,
            website_url=validated_url,
            canonical_domain=canonical_domain,
            address=representative.address,
            industry=normalized_cohort,
            latitude=representative.latitude,
            longitude=representative.longitude,
            discovered_at=representative.discovered_at,
            updated_at=representative.discovered_at,
            source_urls=[item.source_url for item in observations],
        )
        disposition = DiscoverySeedDisposition.INSERTED
        persisted = candidate

        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT snapshot_json, normalized_name, normalized_address
                FROM candidates WHERE canonical_domain = ?
                """,
                (canonical_domain,),
            ).fetchone()
            matched_domain = row is not None
            if row is None:
                row = self.connection.execute(
                    """
                    SELECT c.snapshot_json, c.normalized_name, c.normalized_address
                    FROM candidate_sources AS s
                    JOIN candidates AS c ON c.candidate_id = s.candidate_id
                    WHERE s.canonical_domain = ?
                    LIMIT 1
                    """,
                    (canonical_domain,),
                ).fetchone()
                matched_domain = row is not None
            if row is None and normalized_address is not None:
                row = self.connection.execute(
                    """
                    SELECT snapshot_json, normalized_name, normalized_address
                    FROM candidates
                    WHERE normalized_name = ? AND normalized_address = ?
                    """,
                    (normalized_name, normalized_address),
                ).fetchone()
            if row is None:
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
                        _utc_text(representative.discovered_at),
                        _utc_text(representative.discovered_at),
                        _canonical_json(candidate),
                    ),
                )
            else:
                persisted = self._candidate_from_row(row)
                disposition = DiscoverySeedDisposition.DUPLICATE
            for observation in observations:
                observation_address = (
                    _normalize_match_text(observation.address)
                    if observation.address is not None
                    else None
                )
                persisted_address = row["normalized_address"] if row is not None else None
                contradictory = row is not None and matched_domain and (
                    str(row["normalized_name"])
                    != _normalize_match_text(observation.business_name)
                    or (
                        persisted_address is not None
                        and observation_address is not None
                        and str(persisted_address) != observation_address
                    )
                )
                if contradictory:
                    disposition = DiscoverySeedDisposition.CONFLICT
                self._append_discovery_seed_source(
                    persisted.candidate_id,
                    canonical_domain,
                    observation,
                    normalized_cohort,
                    conflict=contradictory,
                )

        return DiscoverySeedUpsertResult(disposition, persisted)

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
        persisted = candidate
        contradictory_observation = False

        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT snapshot_json, normalized_name, normalized_address
                FROM candidates WHERE canonical_domain = ?
                """,
                (canonical_domain,),
            ).fetchone()
            matched_domain = row is not None
            if row is None:
                row = self.connection.execute(
                    """
                    SELECT c.snapshot_json, c.normalized_name, c.normalized_address
                    FROM candidate_sources AS s
                    JOIN candidates AS c ON c.candidate_id = s.candidate_id
                    WHERE s.canonical_domain = ?
                    LIMIT 1
                    """,
                    (canonical_domain,),
                ).fetchone()
                matched_domain = row is not None
            if row is None and normalized_address is not None:
                row = self.connection.execute(
                    """
                    SELECT snapshot_json, normalized_name, normalized_address
                    FROM candidates
                    WHERE normalized_name = ? AND normalized_address = ?
                    """,
                    (normalized_name, normalized_address),
                ).fetchone()
            if row is not None:
                persisted = self._candidate_from_row(row)
                persisted_address = row["normalized_address"]
                contradictory_observation = matched_domain and (
                    str(row["normalized_name"]) != normalized_name
                    or (
                        persisted_address is not None
                        and normalized_address is not None
                        and str(persisted_address) != normalized_address
                    )
                )
            else:
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
            self._append_candidate_source(
                persisted.candidate_id,
                validated_url,
                canonical_domain,
                business_name,
                address,
                normalized_name,
                normalized_address,
                now,
                conflict=contradictory_observation,
            )

        if contradictory_observation:
            raise RepositoryConflict(
                "candidate domain received a materially contradictory name or address observation"
            )
        return persisted

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
                WHERE (
                        run_locks.owner = excluded.owner
                    AND run_locks.acquired_at <= excluded.acquired_at
                    AND run_locks.lease_expires_at <= excluded.lease_expires_at
                ) OR (
                        run_locks.owner <> excluded.owner
                    AND run_locks.lease_expires_at < excluded.acquired_at
                )
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
                WHERE lock_key = ?
                  AND owner = ?
                  AND lease_expires_at >= ?
                  AND acquired_at <= ?
                  AND lease_expires_at <= ?
                """,
                (
                    now_text,
                    expiry_text,
                    lock_key,
                    owner_token,
                    now_text,
                    now_text,
                    expiry_text,
                ),
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
        normalized_score = ScoreRecord.model_validate(
            {
                **score.model_dump(mode="python"),
                "evidence_ids": sorted(set(score.evidence_ids)),
            }
        )
        snapshot = _canonical_json(normalized_score)
        identity_json = _canonical_mapping_json(
            {
                "evidence_ids": list(normalized_score.evidence_ids),
                "inputs": normalized_score.model_dump(mode="json")["inputs"],
                "rubric_version": normalized_score.rubric_version,
                "score_name": normalized_score.score_name,
            }
        )
        record_id = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
        self._append_snapshot(
            table="scores",
            identity_column="record_id",
            record_id=record_id,
            model=normalized_score,
            model_type=ScoreRecord,
            insert_sql="""
                INSERT INTO scores (record_id, score_name, rubric_version, snapshot_json)
                VALUES (?, ?, ?, ?)
            """,
            values=(
                record_id,
                normalized_score.score_name,
                normalized_score.rubric_version,
                snapshot,
            ),
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
                if _delivery_immutable_identity(persisted) != _delivery_immutable_identity(delivery):
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

    def enqueue_delivery_once(self, delivery: DeliveryRecord) -> bool:
        """Idempotently enqueue a delivery and report whether it was newly inserted."""

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
                if _delivery_immutable_identity(persisted) != _delivery_immutable_identity(delivery):
                    raise RepositoryConflict(
                        "delivery identity already exists with different immutable content"
                    )
                return False
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
            return True

    def get_delivery(self, delivery_id: str) -> DeliveryRecord:
        row = self.connection.execute(
            "SELECT snapshot_json FROM deliveries WHERE delivery_id = ?",
            (_require_nonblank(delivery_id, "delivery_id"),),
        ).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        return _deserialize(DeliveryRecord, str(row["snapshot_json"]))

    def get_delivery_status(self, idempotency_key: str) -> DeliveryState | None:
        row = self.connection.execute(
            "SELECT status FROM deliveries WHERE idempotency_key = ?",
            (_require_nonblank(idempotency_key, "idempotency_key"),),
        ).fetchone()
        return None if row is None else DeliveryState(str(row["status"]))

    def transition_delivery(
        self,
        delivery_id: str,
        expected: DeliveryRecord,
        replacement: DeliveryRecord,
    ) -> DeliveryRecord:
        """Compare-and-swap a delivery using its expected persisted snapshot."""

        identity = _require_nonblank(delivery_id, "delivery_id")
        if not isinstance(expected, DeliveryRecord):
            raise TypeError("expected must be a DeliveryRecord")
        expected_state = expected.status
        if replacement.delivery_id != identity:
            raise ValueError("replacement delivery_id must match delivery_id")
        snapshot = _canonical_json(replacement)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT snapshot_json FROM deliveries WHERE delivery_id = ?",
                (identity,),
            ).fetchone()
            if row is None:
                raise KeyError(identity)
            persisted = _deserialize(DeliveryRecord, str(row["snapshot_json"]))
            if _delivery_immutable_identity(persisted) != _delivery_immutable_identity(replacement):
                raise RepositoryConflict("delivery immutable content changed")
            if persisted.status is not expected_state:
                raise RepositoryConflict("delivery state changed before transition")
            if persisted != expected:
                raise RepositoryConflict("delivery snapshot changed before transition")
            persisted_snapshot = str(row["snapshot_json"])
            cursor = self.connection.execute(
                """
                UPDATE deliveries
                SET status = ?, snapshot_json = ?
                WHERE delivery_id = ? AND status = ?
                  AND snapshot_json = ?
                """,
                (
                    replacement.status.value,
                    snapshot,
                    identity,
                    expected_state.value,
                    persisted_snapshot,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("delivery state changed before transition")
        return replacement

    def claim_next_delivery(self, *, max_attempts: int = 2) -> DeliveryRecord | None:
        """Atomically claim the oldest eligible pending or retryable failed delivery."""

        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT delivery_id, snapshot_json
                FROM deliveries
                WHERE status = 'pending'
                   OR (status = 'failed'
                       AND CAST(json_extract(snapshot_json, '$.attempt_count') AS INTEGER) < ?)
                ORDER BY rowid
                LIMIT 1
                """,
                (max_attempts,),
            ).fetchone()
            if row is None:
                return None
            persisted = _deserialize(DeliveryRecord, str(row["snapshot_json"]))
            claimed = persisted.validated_replace(status=DeliveryState.SENDING)
            cursor = self.connection.execute(
                """
                UPDATE deliveries
                SET status = 'sending', snapshot_json = ?
                WHERE delivery_id = ? AND status = ?
                """,
                (_canonical_json(claimed), persisted.delivery_id, persisted.status.value),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("delivery state changed before claim")
            return claimed

    def ensure_review_project(self, project: ReviewProjectRecord) -> bool:
        """Persist one production review project with immutable, restart-safe identity."""

        project_id = _require_nonblank(project.project_id, "project_id")
        candidate_id = _require_nonblank(project.candidate_id, "candidate_id")
        snapshot = _canonical_mapping_json(
            {
                "artifact_dir": _require_nonblank(project.artifact_dir, "artifact_dir"),
                "candidate_id": candidate_id,
                "created_at": _utc_text(project.created_at),
                "market_id": _require_nonblank(project.market_id, "market_id"),
                "project_id": project_id,
                "state": ProjectState.REVIEW.value,
                "state_version": 0,
            }
        )
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT candidate_id, snapshot_json FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if row is not None:
                persisted_snapshot = json.loads(str(row["snapshot_json"]))
                if not isinstance(persisted_snapshot, dict):
                    raise RepositoryError("project snapshot must be a JSON object")
                immutable_snapshot = {
                    key: value
                    for key, value in persisted_snapshot.items()
                    if key not in {"state", "state_version"}
                }
                expected_snapshot = json.loads(snapshot)
                expected_immutable = {
                    key: value
                    for key, value in expected_snapshot.items()
                    if key not in {"state", "state_version"}
                }
                if (
                    str(row["candidate_id"]) != candidate_id
                    or immutable_snapshot != expected_immutable
                ):
                    raise RepositoryConflict(
                        "project identity already exists with different immutable content"
                    )
                return False
            self.connection.execute(
                """
                INSERT INTO projects (
                    project_id, candidate_id, state, state_version, snapshot_json
                ) VALUES (?, ?, ?, 0, ?)
                """,
                (project_id, candidate_id, ProjectState.REVIEW.value, snapshot),
            )
        return True

    def synchronize_project_state(
        self,
        project_id: str,
        *,
        expected_version: int,
        state: ProjectState,
        state_version: int,
    ) -> bool:
        """Synchronize coordinator-owned state only when the persisted version still matches."""

        identity = _require_nonblank(project_id, "project_id")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise ValueError("expected_version must be a non-negative integer")
        if (
            isinstance(state_version, bool)
            or not isinstance(state_version, int)
            or state_version <= expected_version
        ):
            raise ValueError("state_version must be greater than expected_version")
        if not isinstance(state, ProjectState):
            raise TypeError("state must be a ProjectState")
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT snapshot_json FROM projects WHERE project_id = ? AND state_version = ?",
                (identity, expected_version),
            ).fetchone()
            if row is None:
                return False
            snapshot = json.loads(str(row["snapshot_json"]))
            if not isinstance(snapshot, dict):
                raise RepositoryError("project snapshot must be a JSON object")
            snapshot.update({"state": state.value, "state_version": state_version})
            cursor = self.connection.execute(
                """
                UPDATE projects
                SET state = ?, state_version = ?, snapshot_json = ?
                WHERE project_id = ? AND state_version = ?
                """,
                (
                    state.value,
                    state_version,
                    _canonical_mapping_json(snapshot),
                    identity,
                    expected_version,
                ),
            )
            return cursor.rowcount == 1

    def insert_component_set(self, component: ComponentSet) -> ComponentSet:
        """Persist one bot-owned component set without changing its identity."""

        snapshot = _canonical_json(component)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT snapshot_json FROM component_sets
                WHERE component_set_id = ? OR (channel_id = ? AND message_id = ?)
                LIMIT 1
                """,
                (component.component_set_id, component.channel_id, component.message_id),
            ).fetchone()
            if row is not None:
                persisted = _deserialize(ComponentSet, str(row["snapshot_json"]))
                if persisted != component:
                    raise RepositoryConflict(
                        "component identity already exists with different immutable content"
                    )
                return persisted
            self.connection.execute(
                """
                INSERT INTO component_sets (
                    component_set_id, channel_id, message_id, project_id,
                    expires_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    component.component_set_id,
                    component.channel_id,
                    component.message_id,
                    component.project_id,
                    _utc_text(component.expires_at),
                    snapshot,
                ),
            )
        return component

    def get_component_set(self, channel_id: str, message_id: str) -> ComponentSet | None:
        row = self.connection.execute(
            """
            SELECT snapshot_json FROM component_sets
            WHERE channel_id = ? AND message_id = ?
            """,
            (_require_nonblank(channel_id, "channel_id"), _require_nonblank(message_id, "message_id")),
        ).fetchone()
        return None if row is None else _deserialize(ComponentSet, str(row["snapshot_json"]))

    def get_component_set_by_message_id(self, message_id: str) -> ComponentSet | None:
        """Resolve one bot message globally and fail closed on legacy ambiguity."""

        rows = self.connection.execute(
            """
            SELECT snapshot_json FROM component_sets
            WHERE message_id = ?
            LIMIT 2
            """,
            (_require_nonblank(message_id, "message_id"),),
        ).fetchall()
        if len(rows) > 1:
            raise RepositoryConflict("component message identity is ambiguous")
        return (
            None
            if not rows
            else _deserialize(ComponentSet, str(rows[0]["snapshot_json"]))
        )

    def get_project_state_version(self, project_id: str) -> int:
        row = self.connection.execute(
            "SELECT state_version FROM projects WHERE project_id = ?",
            (_require_nonblank(project_id, "project_id"),),
        ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return int(row["state_version"])

    def claim_component_action(self, component_set_id: str, actor_id: str, action: str) -> bool:
        """Atomically claim a callback identity across process restarts."""

        with _immediate_transaction(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO component_actions (
                    component_set_id, actor_id, action, claimed_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(component_set_id, actor_id, action) DO NOTHING
                """,
                (
                    _require_nonblank(component_set_id, "component_set_id"),
                    _require_nonblank(actor_id, "actor_id"),
                    _require_nonblank(action, "action"),
                    _utc_text(datetime.now(UTC)),
                ),
            )
            return cursor.rowcount == 1

    def release_component_action(self, component_set_id: str, actor_id: str, action: str) -> None:
        """Release a claim when coordinator execution failed before applying the action."""

        with _immediate_transaction(self.connection):
            self.connection.execute(
                """
                DELETE FROM component_actions
                WHERE component_set_id = ? AND actor_id = ? AND action = ?
                  AND confirmed_state IS NULL AND confirmed_state_version IS NULL
                """,
                (
                    _require_nonblank(component_set_id, "component_set_id"),
                    _require_nonblank(actor_id, "actor_id"),
                    _require_nonblank(action, "action"),
                ),
            )

    def confirm_component_action(
        self,
        component_set_id: str,
        actor_id: str,
        action: str,
        *,
        state: ProjectState,
        state_version: int,
    ) -> None:
        """Durably record coordinator authority before best-effort SQLite synchronization."""

        if not isinstance(state, ProjectState):
            raise TypeError("state must be a ProjectState")
        if (
            isinstance(state_version, bool)
            or not isinstance(state_version, int)
            or state_version < 0
        ):
            raise ValueError("state_version must be a non-negative integer")
        identity = (
            _require_nonblank(component_set_id, "component_set_id"),
            _require_nonblank(actor_id, "actor_id"),
            _require_nonblank(action, "action"),
        )
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT confirmed_state, confirmed_state_version
                FROM component_actions
                WHERE component_set_id = ? AND actor_id = ? AND action = ?
                """,
                identity,
            ).fetchone()
            if row is None:
                raise KeyError(identity)
            persisted = (row["confirmed_state"], row["confirmed_state_version"])
            confirmation = (state.value, state_version)
            if persisted[0] is not None or persisted[1] is not None:
                if persisted != confirmation:
                    raise RepositoryConflict(
                        "component action already has a different coordinator confirmation"
                    )
                return
            cursor = self.connection.execute(
                """
                UPDATE component_actions
                SET confirmed_state = ?, confirmed_state_version = ?
                WHERE component_set_id = ? AND actor_id = ? AND action = ?
                  AND confirmed_state IS NULL AND confirmed_state_version IS NULL
                """,
                (*confirmation, *identity),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("component action confirmation changed before update")

    def reconcile_component_action(
        self, component_set_id: str, actor_id: str, action: str
    ) -> bool:
        """Apply a durable coordinator confirmation to lagging SQLite project state."""

        identity = (
            _require_nonblank(component_set_id, "component_set_id"),
            _require_nonblank(actor_id, "actor_id"),
            _require_nonblank(action, "action"),
        )
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT cs.project_id, ca.confirmed_state, ca.confirmed_state_version
                FROM component_actions AS ca
                JOIN component_sets AS cs ON cs.component_set_id = ca.component_set_id
                WHERE ca.component_set_id = ? AND ca.actor_id = ? AND ca.action = ?
                """,
                identity,
            ).fetchone()
            if row is None or row["confirmed_state"] is None:
                return False
            state = ProjectState(str(row["confirmed_state"]))
            state_version = int(row["confirmed_state_version"])
            project_id = str(row["project_id"])
            project = self.connection.execute(
                "SELECT state, state_version, snapshot_json FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise KeyError(project_id)
            current_version = int(project["state_version"])
            if current_version > state_version:
                return True
            if current_version == state_version:
                if str(project["state"]) != state.value:
                    raise RepositoryConflict(
                        "coordinator confirmation conflicts with synchronized project state"
                    )
                return True
            snapshot = json.loads(str(project["snapshot_json"]))
            if not isinstance(snapshot, dict):
                raise RepositoryError("project snapshot must be a JSON object")
            snapshot.update({"state": state.value, "state_version": state_version})
            cursor = self.connection.execute(
                """
                UPDATE projects SET state = ?, state_version = ?, snapshot_json = ?
                WHERE project_id = ? AND state_version < ?
                """,
                (
                    state.value,
                    state_version,
                    _canonical_mapping_json(snapshot),
                    project_id,
                    state_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("project state changed during reconciliation")
            return True

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
