"""Transactional repositories for canonical workflow records."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, Self, TypeVar

import tldextract
from pydantic import AnyHttpUrl, BaseModel, TypeAdapter

from openclaw_web.lead_contracts import (
    PortfolioEntry,
    PortfolioEntryState,
    StageOutcome,
    StageStatus,
)
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


@dataclass(frozen=True, slots=True)
class DashboardActionReceipt:
    """Durable dashboard action claim/result used for replay protection."""

    idempotency_key: str
    action: str
    target_id: str
    actor_id: str
    expected_state_version: int
    status: str
    event_id: str
    response: dict[str, Any] | None
    error_code: str | None


class ReviewProjectRecord(Protocol):
    """Structural input accepted from the production pipeline without an import cycle."""

    project_id: str
    candidate_id: str
    market_id: str
    artifact_dir: str
    created_at: datetime


_ModelT = TypeVar("_ModelT", bound=BaseModel)
_WEB_URL_ADAPTER = TypeAdapter(WebUrl)
_PROJECT_SNAPSHOT_SYNC_KEYS = frozenset({"lead_state", "pages"})
_PAGE_SNAPSHOT_SYNC_KEYS = frozenset(
    {
        "slug",
        "status",
        "owner_id",
        "assignee_id",
        "owner",
        "assignee",
        "checklist_complete",
        "next_action",
    }
)
_SAFE_SNAPSHOT_CODE = re.compile(r"^[A-Za-z0-9_.:@/ +()\-]{1,200}$")
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


def _validated_project_snapshot_updates(
    updates: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate the tiny snapshot subset the dashboard read model may receive."""

    if updates is None:
        return {}
    if not isinstance(updates, Mapping):
        raise TypeError("snapshot_updates must be a mapping")
    unknown = set(updates) - _PROJECT_SNAPSHOT_SYNC_KEYS
    if unknown:
        raise ValueError(f"snapshot_updates contains non-allowlisted field: {min(unknown)}")

    normalized: dict[str, Any] = {}
    if "lead_state" in updates:
        lead_state = updates["lead_state"]
        allowed_states = {state.value for state in PortfolioEntryState}
        if not isinstance(lead_state, str) or lead_state not in allowed_states:
            raise ValueError("snapshot_updates.lead_state is invalid")
        normalized["lead_state"] = lead_state

    if "pages" in updates:
        pages = updates["pages"]
        if not isinstance(pages, (list, tuple)) or len(pages) > 50:
            raise ValueError("snapshot_updates.pages must contain at most 50 pages")
        normalized_pages: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, Mapping):
                raise TypeError("snapshot_updates.pages must contain objects")
            unknown_page = set(page) - _PAGE_SNAPSHOT_SYNC_KEYS
            if unknown_page:
                raise ValueError(
                    f"snapshot_updates.pages contains non-allowlisted field: {min(unknown_page)}"
                )
            slug = page.get("slug")
            status = page.get("status")
            if (
                not isinstance(slug, str)
                or not slug.strip()
                or _SAFE_SNAPSHOT_CODE.fullmatch(slug) is None
                or not isinstance(status, str)
                or not status.strip()
                or _SAFE_SNAPSHOT_CODE.fullmatch(status) is None
            ):
                raise ValueError("snapshot_updates.pages requires safe slug and status")
            normalized_page: dict[str, Any] = {"slug": slug, "status": status}
            for key in _PAGE_SNAPSHOT_SYNC_KEYS - {"slug", "status"}:
                if key not in page or page[key] is None:
                    continue
                value = page[key]
                if key == "checklist_complete":
                    if not isinstance(value, bool):
                        raise TypeError("snapshot_updates.checklist_complete must be boolean")
                elif not isinstance(value, str) or _SAFE_SNAPSHOT_CODE.fullmatch(value) is None:
                    raise ValueError(f"snapshot_updates.pages.{key} is invalid")
                normalized_page[key] = value
            normalized_pages.append(normalized_page)
        normalized["pages"] = normalized_pages
    return normalized


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

    def _refresh_discovery_source_urls(self, candidate: Candidate) -> Candidate:
        """Keep the candidate snapshot aligned with intact discovery observations."""

        rows = self.connection.execute(
            """
            SELECT DISTINCT source_url
            FROM candidate_sources
            WHERE candidate_id = ? AND conflict = 0
            ORDER BY source_url
            """,
            (candidate.candidate_id,),
        ).fetchall()
        source_urls = [str(row["source_url"]) for row in rows]
        if not source_urls:
            return candidate
        refreshed = candidate.validated_replace(source_urls=source_urls)
        self.connection.execute(
            "UPDATE candidates SET snapshot_json = ? WHERE candidate_id = ?",
            (_canonical_json(refreshed), candidate.candidate_id),
        )
        return refreshed

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
            persisted = self._refresh_discovery_source_urls(persisted)

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

    def get_run_record(self, run_id: str) -> RunRecord | None:
        """Load one canonical run without exposing its raw database snapshot."""

        identity = _require_nonblank(run_id, "run_id")
        row = self.connection.execute(
            "SELECT snapshot_json FROM runs WHERE run_id = ?", (identity,)
        ).fetchone()
        return None if row is None else _deserialize(RunRecord, str(row["snapshot_json"]))

    @staticmethod
    def _lead_stage_outcomes(run: RunRecord) -> tuple[StageOutcome, ...]:
        payload = run.model_dump(mode="json")
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise RepositoryError("run metadata must be a JSON object")
        raw_outcomes = metadata.get("lead_stage_outcomes", [])
        if not isinstance(raw_outcomes, list):
            raise RepositoryError("lead_stage_outcomes must be a JSON array")
        outcomes: list[StageOutcome] = []
        for raw in raw_outcomes:
            if not isinstance(raw, dict):
                raise RepositoryError("lead stage outcome must be a JSON object")
            try:
                outcome = StageOutcome.model_validate_json(
                    json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise RepositoryError("lead stage outcome is invalid") from error
            if outcome.run_id != run.run_id:
                raise RepositoryError("lead stage outcome belongs to another run")
            outcomes.append(outcome)
        return tuple(outcomes)

    def get_stage_outcomes(self, run_id: str) -> tuple[StageOutcome, ...]:
        """Return the typed lead-stage checkpoint set stored in a run snapshot."""

        run = self.get_run_record(run_id)
        return () if run is None else self._lead_stage_outcomes(run)

    def record_stage_outcome(
        self,
        outcome: StageOutcome,
        *,
        current_stage: str | None = None,
        run_status: str | None = None,
    ) -> None:
        """Atomically replace one retryable stage result in the run checkpoint.

        Lead-stage outcomes live under the run's typed metadata so legacy
        ``StageRecord`` consumers and the public ``ProductionPipeline`` façade
        remain unchanged. A completed outcome is immutable; a retryable outcome
        may be replaced by its later successful attempt for the same input hash.
        """

        if not isinstance(outcome, StageOutcome):
            raise TypeError("outcome must be a StageOutcome")
        stage = _require_nonblank(
            current_stage or outcome.stage_id.split(":", 1)[0], "current_stage"
        )
        requested_status = None if run_status is None else _require_nonblank(run_status, "run_status")
        persisted_outcome = outcome.validated_replace(reused=False)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT status, snapshot_json FROM runs WHERE run_id = ?", (outcome.run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(outcome.run_id)
            try:
                run = _deserialize(RunRecord, str(row["snapshot_json"]))
            except (TypeError, ValueError) as error:
                raise RepositoryError("run snapshot is invalid") from error
            outcomes = list(self._lead_stage_outcomes(run))
            for index, existing in enumerate(outcomes):
                if existing.stage_id != persisted_outcome.stage_id:
                    continue
                if existing == persisted_outcome:
                    break
                if existing.status is StageStatus.COMPLETE:
                    raise RepositoryConflict("completed stage outcome cannot be replaced")
                outcomes[index] = persisted_outcome
                break
            else:
                outcomes.append(persisted_outcome)

            payload = run.model_dump(mode="python")
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                raise RepositoryError("run metadata must be a JSON object")
            metadata["lead_stage_outcomes"] = [
                item.model_dump(mode="json") for item in outcomes
            ]
            payload["metadata"] = metadata
            payload["current_stage"] = stage
            existing_status = str(row["status"])
            effective_status = requested_status or existing_status
            if requested_status is None and outcome.status is not StageStatus.COMPLETE:
                effective_status = outcome.status.value
            elif requested_status is None and existing_status == "pending":
                effective_status = "running"
            payload["status"] = effective_status
            try:
                updated_run = RunRecord.model_validate(payload)
            except (TypeError, ValueError) as error:
                raise RepositoryError("updated run snapshot is invalid") from error
            cursor = self.connection.execute(
                """
                UPDATE runs
                SET status = ?, current_stage = ?, snapshot_json = ?
                WHERE run_id = ? AND snapshot_json = ?
                """,
                (
                    effective_status,
                    stage,
                    _canonical_json(updated_run),
                    outcome.run_id,
                    str(row["snapshot_json"]),
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("run snapshot changed before stage outcome update")

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
        snapshot_updates: Mapping[str, Any] | None = None,
    ) -> bool:
        """Synchronize coordinator state and an allowlisted read-model snapshot subset."""

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
        safe_updates = _validated_project_snapshot_updates(snapshot_updates)
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
            snapshot.update(safe_updates)
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

    @staticmethod
    def _portfolio_entry_snapshot(entry: PortfolioEntry) -> str:
        return json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def create_portfolio(
        self,
        *,
        portfolio_id: str,
        run_id: str | None,
        status: str,
        entries: tuple[PortfolioEntry, ...] | list[PortfolioEntry],
        target: int = 5,
        maximum: int = 7,
        created_at: datetime | None = None,
    ) -> bool:
        """Persist one portfolio and all entries in one transaction.

        Replaying an identical portfolio is a no-op.  A same-ID portfolio with
        different immutable content is a conflict, preserving idempotency.
        """

        identity = _require_nonblank(portfolio_id, "portfolio_id")
        if run_id is not None:
            run_id = _require_nonblank(run_id, "run_id")
        state = _require_nonblank(status, "status")
        if isinstance(target, bool) or not isinstance(target, int) or not 3 <= target <= 7:
            raise ValueError("target must be between 3 and 7")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not target <= maximum <= 7:
            raise ValueError("maximum must be between target and 7")
        values = tuple(entries)
        if len(values) > maximum:
            raise ValueError("portfolio cannot contain more than maximum entries")
        if any(not isinstance(entry, PortfolioEntry) for entry in values):
            raise TypeError("entries must contain PortfolioEntry values")
        if any(entry.portfolio_id != identity for entry in values):
            raise ValueError("portfolio_id on every entry must match the portfolio")
        now = created_at or datetime.now(UTC)
        now_text = _utc_text(now)
        snapshot = _canonical_mapping_json(
            {
                "portfolio_id": identity,
                "run_id": run_id,
                "status": state,
                "target": target,
                "maximum": maximum,
                "state_version": 0,
                "entry_ids": [entry.entry_id for entry in values],
                "entry_snapshots": [
                    json.loads(self._portfolio_entry_snapshot(entry)) for entry in values
                ],
            }
        )
        with _immediate_transaction(self.connection):
            existing = self.connection.execute(
                "SELECT snapshot_json FROM portfolios WHERE portfolio_id = ?", (identity,)
            ).fetchone()
            if existing is not None:
                if str(existing["snapshot_json"]) != snapshot:
                    raise RepositoryConflict("portfolio identity already exists with different content")
                return False
            self.connection.execute(
                """
                INSERT INTO portfolios (
                    portfolio_id, run_id, status, target, maximum, state_version,
                    created_at, updated_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (identity, run_id, state, target, maximum, now_text, now_text, snapshot),
            )
            for entry in values:
                self.connection.execute(
                    """
                    INSERT INTO portfolio_entries (
                        entry_id, portfolio_id, candidate_id, rank, state,
                        state_version, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.entry_id,
                        identity,
                        entry.candidate_id,
                        entry.rank,
                        entry.state.value,
                        entry.state_version,
                        self._portfolio_entry_snapshot(entry),
                    ),
                )
        return True

    def get_portfolio_entries(self, portfolio_id: str) -> tuple[PortfolioEntry, ...]:
        identity = _require_nonblank(portfolio_id, "portfolio_id")
        rows = self.connection.execute(
            "SELECT snapshot_json FROM portfolio_entries WHERE portfolio_id = ? ORDER BY rank, entry_id",
            (identity,),
        ).fetchall()
        return tuple(PortfolioEntry.model_validate_json(str(row["snapshot_json"])) for row in rows)

    def get_portfolio_entry(self, entry_id: str) -> PortfolioEntry | None:
        identity = _require_nonblank(entry_id, "entry_id")
        row = self.connection.execute(
            "SELECT snapshot_json FROM portfolio_entries WHERE entry_id = ?", (identity,)
        ).fetchone()
        return None if row is None else PortfolioEntry.model_validate_json(str(row["snapshot_json"]))

    def get_portfolio(self, portfolio_id: str) -> dict[str, Any] | None:
        identity = _require_nonblank(portfolio_id, "portfolio_id")
        row = self.connection.execute(
            "SELECT portfolio_id, run_id, status, target, maximum, state_version, "
            "created_at, updated_at, snapshot_json FROM portfolios WHERE portfolio_id = ?",
            (identity,),
        ).fetchone()
        if row is None:
            return None
        return {
            "portfolio_id": str(row["portfolio_id"]),
            "run_id": None if row["run_id"] is None else str(row["run_id"]),
            "status": str(row["status"]),
            "target": int(row["target"]),
            "maximum": int(row["maximum"]),
            "state_version": int(row["state_version"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "entries": self.get_portfolio_entries(identity),
        }

    def record_portfolio_delivery(
        self,
        *,
        portfolio_id: str,
        delivery_id: str,
        idempotency_key: str,
        status: str,
        snapshot: dict[str, Any] | None = None,
    ) -> bool:
        portfolio = _require_nonblank(portfolio_id, "portfolio_id")
        delivery = _require_nonblank(delivery_id, "delivery_id")
        key = _require_nonblank(idempotency_key, "idempotency_key")
        delivery_status = _require_nonblank(status, "status")
        if delivery_status not in {"pending", "sent", "failed"}:
            raise ValueError("portfolio delivery status must be pending, sent or failed")
        now_text = _utc_text(datetime.now(UTC))
        payload = dict(snapshot or {})
        payload.update(
            {
                "portfolio_id": portfolio,
                "delivery_id": delivery,
                "idempotency_key": key,
                "status": delivery_status,
            }
        )
        snapshot_json = _canonical_mapping_json(payload)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT snapshot_json FROM portfolio_deliveries WHERE portfolio_id = ?",
                (portfolio,),
            ).fetchone()
            if row is not None:
                try:
                    existing_payload = json.loads(str(row["snapshot_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise RepositoryError("portfolio delivery snapshot is invalid") from error
                if not isinstance(existing_payload, dict):
                    raise RepositoryError("portfolio delivery snapshot must be an object")
                immutable_existing = {
                    key: value
                    for key, value in existing_payload.items()
                    if key not in {"status", "created_at", "updated_at"}
                }
                immutable_incoming = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"status", "created_at", "updated_at"}
                }
                if immutable_existing != immutable_incoming:
                    raise RepositoryConflict("portfolio delivery already exists with different content")
                return False
            self.connection.execute(
                """
                INSERT INTO portfolio_deliveries (
                    portfolio_id, delivery_id, idempotency_key, status,
                    created_at, updated_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (portfolio, delivery, key, delivery_status, now_text, now_text, snapshot_json),
            )
        return True

    def get_portfolio_delivery(self, portfolio_id: str) -> dict[str, Any] | None:
        """Return the durable delivery envelope for one portfolio."""

        portfolio = _require_nonblank(portfolio_id, "portfolio_id")
        row = self.connection.execute(
            """
            SELECT portfolio_id, delivery_id, idempotency_key, status,
                   created_at, updated_at, snapshot_json
            FROM portfolio_deliveries WHERE portfolio_id = ?
            """,
            (portfolio,),
        ).fetchone()
        if row is None:
            return None
        try:
            snapshot = json.loads(str(row["snapshot_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RepositoryError("portfolio delivery snapshot is invalid") from error
        if not isinstance(snapshot, dict):
            raise RepositoryError("portfolio delivery snapshot must be an object")
        return {
            "portfolio_id": str(row["portfolio_id"]),
            "delivery_id": str(row["delivery_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "snapshot": snapshot,
        }

    def transition_portfolio_delivery(
        self,
        portfolio_id: str,
        *,
        expected_status: str,
        status: str,
    ) -> bool:
        """Compare-and-swap a delivery status without changing its message identity."""

        portfolio = _require_nonblank(portfolio_id, "portfolio_id")
        expected = _require_nonblank(expected_status, "expected_status")
        replacement = _require_nonblank(status, "status")
        if expected not in {"pending", "sent", "failed"} or replacement not in {
            "pending",
            "sent",
            "failed",
        }:
            raise ValueError("portfolio delivery status must be pending, sent or failed")
        now_text = _utc_text(datetime.now(UTC))
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT status, snapshot_json FROM portfolio_deliveries WHERE portfolio_id = ?",
                (portfolio,),
            ).fetchone()
            if row is None:
                raise KeyError(portfolio)
            current = str(row["status"])
            if current == replacement:
                return True
            if current != expected:
                raise RepositoryConflict("portfolio delivery state changed before transition")
            try:
                payload = json.loads(str(row["snapshot_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise RepositoryError("portfolio delivery snapshot is invalid") from error
            if not isinstance(payload, dict):
                raise RepositoryError("portfolio delivery snapshot must be an object")
            payload["status"] = replacement
            payload["updated_at"] = now_text
            cursor = self.connection.execute(
                """
                UPDATE portfolio_deliveries
                SET status = ?, updated_at = ?, snapshot_json = ?
                WHERE portfolio_id = ? AND status = ? AND snapshot_json = ?
                """,
                (
                    replacement,
                    now_text,
                    _canonical_mapping_json(payload),
                    portfolio,
                    expected,
                    str(row["snapshot_json"]),
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("portfolio delivery state changed before transition")
        return True

    @staticmethod
    def _dashboard_receipt(row: sqlite3.Row) -> DashboardActionReceipt:
        response: dict[str, Any] | None = None
        if row["response_json"] is not None:
            value = json.loads(str(row["response_json"]))
            if not isinstance(value, dict):
                raise RepositoryError("dashboard action response must be an object")
            response = value
        return DashboardActionReceipt(
            idempotency_key=str(row["idempotency_key"]),
            action=str(row["action"]),
            target_id=str(row["target_id"]),
            actor_id=str(row["actor_id"]),
            expected_state_version=int(row["expected_state_version"]),
            status=str(row["status"]),
            event_id=str(row["event_id"] or ""),
            response=response,
            error_code=None if row["error_code"] is None else str(row["error_code"]),
        )

    def claim_dashboard_action(
        self,
        *,
        idempotency_key: str,
        action: str,
        target_id: str,
        actor_id: str,
        expected_state_version: int,
    ) -> DashboardActionReceipt:
        key = _require_nonblank(idempotency_key, "idempotency_key")
        action_name = _require_nonblank(action, "action")
        target = _require_nonblank(target_id, "target_id")
        actor = _require_nonblank(actor_id, "actor_id")
        if (
            isinstance(expected_state_version, bool)
            or not isinstance(expected_state_version, int)
            or expected_state_version < 0
        ):
            raise ValueError("expected_state_version must be a non-negative integer")
        now_text = _utc_text(datetime.now(UTC))
        event_id = f"dashboard-event-{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}"
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is not None:
                same_identity = (
                    str(row["action"]) == action_name
                    and str(row["target_id"]) == target
                    and str(row["actor_id"]) == actor
                    and int(row["expected_state_version"]) == expected_state_version
                )
                if not same_identity:
                    raise RepositoryConflict("dashboard idempotency key has a different action identity")
                return self._dashboard_receipt(row)
            self.connection.execute(
                """
                INSERT INTO dashboard_action_receipts (
                    idempotency_key, action, target_id, actor_id,
                    expected_state_version, status, event_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (key, action_name, target, actor, expected_state_version, event_id, now_text, now_text),
            )
            inserted = self.connection.execute(
                "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if inserted is None:
                raise RepositoryError("dashboard action receipt disappeared after insert")
            return self._dashboard_receipt(inserted)

    def get_dashboard_action_receipt(self, idempotency_key: str) -> DashboardActionReceipt | None:
        key = _require_nonblank(idempotency_key, "idempotency_key")
        row = self.connection.execute(
            "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return None if row is None else self._dashboard_receipt(row)

    def complete_dashboard_action(
        self,
        idempotency_key: str,
        *,
        status: str,
        response: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> DashboardActionReceipt:
        key = _require_nonblank(idempotency_key, "idempotency_key")
        if status not in {"pending", "succeeded", "failed"}:
            raise ValueError("dashboard action status must be pending, succeeded or failed")
        if response is not None and not isinstance(response, dict):
            raise TypeError("dashboard action response must be an object")
        response_json = None if response is None else _canonical_mapping_json(response)
        now_text = _utc_text(datetime.now(UTC))
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is None:
                raise KeyError(key)
            if str(row["status"]) != "pending":
                if str(row["status"]) != status or str(row["response_json"] or "") != str(response_json or ""):
                    raise RepositoryConflict("dashboard action receipt is already completed")
                return self._dashboard_receipt(row)
            self.connection.execute(
                """
                UPDATE dashboard_action_receipts
                SET status = ?, response_json = ?, error_code = ?, updated_at = ?
                WHERE idempotency_key = ? AND status = 'pending'
                """,
                (status, response_json, error_code, now_text, key),
            )
            updated = self.connection.execute(
                "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if updated is None:
                raise RepositoryError("dashboard action receipt disappeared after update")
            return self._dashboard_receipt(updated)

    def count_candidates(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM candidates").fetchone()
        return int(row["count"])
