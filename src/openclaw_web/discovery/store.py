"""Transactional discovery store for candidates and seed observations."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AnyHttpUrl

from openclaw_web.platform.base import (
    BaseStore,
    _canonical_domain,
    _canonical_json,
    _canonical_mapping_json,
    _deserialize,
    _immediate_transaction,
    _normalize_match_text,
    _require_nonblank,
    _utc_text,
)
from openclaw_web.models import Candidate, CandidateSeed
from openclaw_web.platform.errors import RepositoryConflict


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


class DiscoveryStore(BaseStore):
    """Discovery persistence for seed observations and canonical candidates."""

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
        snapshot = _canonical_mapping_json({**identity, "discovered_at": _utc_text(discovered_at)})
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
        *,
        discovered_at: datetime | None = None,
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
        seed_time = discovered_at if discovered_at is not None else representative.discovered_at
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
            discovered_at=seed_time,
            updated_at=seed_time,
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
                        _utc_text(seed_time),
                        _utc_text(seed_time),
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
                contradictory = (
                    row is not None
                    and matched_domain
                    and (
                        str(row["normalized_name"])
                        != _normalize_match_text(observation.business_name)
                        or (
                            persisted_address is not None
                            and observation_address is not None
                            and str(persisted_address) != observation_address
                        )
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
        address: str | None = None,
        *,
        discovered_at: datetime | None = None,
        state: str = "discovered",
    ) -> Candidate:
        validated_url, canonical_domain = _canonical_domain(url)
        normalized_name = _normalize_match_text(_require_nonblank(business_name, "business_name"))
        normalized_address = (
            _normalize_match_text(_require_nonblank(address, "address"))
            if address is not None
            else None
        )
        now = discovered_at if discovered_at is not None else datetime.now(UTC)
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
                        candidate.state.value
                        if hasattr(candidate.state, "value")
                        else str(candidate.state),
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

    def count_candidates(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM candidates").fetchone()
        return int(row["count"])
