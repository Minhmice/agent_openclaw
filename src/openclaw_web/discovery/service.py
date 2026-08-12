"""Candidate normalization, geofence/cohort processing, and discovery readiness."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

import tldextract
from pydantic import AnyHttpUrl, ValidationError

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url
from openclaw_web.db.repository import DiscoverySeedUpsertResult
from openclaw_web.discovery.base import (
    AutomaticDiscoveryProvider,
    DiscoveryConfigurationError,
    DiscoveryPayloadError,
)
from openclaw_web.discovery.scheduler import assign_cohort
from openclaw_web.geofence import GeofenceResult, GeofenceService, LocationEvidence
from openclaw_web.models import CandidateSeed

_EXTRACTOR = tldextract.TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
    fallback_to_snapshot=True,
    include_psl_private_domains=True,
)


class _SourceObservation(TypedDict):
    source_type: str
    source_url: str
    external_id: str | None
    latitude: float | None
    longitude: float | None
    metadata: dict[str, Any]


class CandidateRepository(Protocol):
    def upsert_discovery_seed(
        self, seed: CandidateSeed, cohort: str
    ) -> DiscoverySeedUpsertResult: ...


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    seed: CandidateSeed
    status: str
    reason: str
    cohort: str | None = None
    geofence: GeofenceResult | None = None
    candidate: object | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryProcessResult:
    candidates: tuple[object, ...]
    outcomes: tuple[DiscoveryOutcome, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryReadiness:
    manual_sources: str
    automatic_discovery: str
    geofence: str
    providers: tuple[tuple[str, str], ...]


class DiscoveryService:
    def __init__(
        self,
        *,
        geofence: GeofenceService | None = None,
        repository: CandidateRepository | None = None,
        providers: Sequence[AutomaticDiscoveryProvider] = (),
        max_seed_candidates: int = 200,
        max_input_seeds: int | None = None,
    ) -> None:
        if (
            isinstance(max_seed_candidates, bool)
            or not isinstance(max_seed_candidates, int)
            or max_seed_candidates <= 0
            or max_seed_candidates > 10_000
        ):
            raise ValueError("max_seed_candidates must be a bounded positive integer")
        self._geofence = geofence
        self._repository = repository
        self._providers = tuple(providers)
        self._cap = max_seed_candidates
        input_cap = (
            max_input_seeds if max_input_seeds is not None else min(1_000, max_seed_candidates * 5)
        )
        if (
            isinstance(input_cap, bool)
            or not isinstance(input_cap, int)
            or input_cap < max_seed_candidates
            or input_cap > 10_000
        ):
            raise ValueError("max_input_seeds must be bounded and at least max_seed_candidates")
        self._input_cap = input_cap

    @staticmethod
    def _identity(url: str) -> str:
        host = AnyHttpUrl(url).host or ""
        host = host.lower().rstrip(".").removeprefix("www.")
        extracted = _EXTRACTOR(host)
        domain = extracted.top_domain_under_public_suffix or host
        return domain.lower()

    def normalize_unique(self, seeds: Iterable[CandidateSeed]) -> tuple[CandidateSeed, ...]:
        if isinstance(seeds, str | bytes | bytearray):
            raise TypeError("seeds must be a non-string iterable")
        grouped: dict[str, list[CandidateSeed]] = {}
        iterator = iter(seeds)
        for index in range(self._input_cap + 1):
            try:
                raw = next(iterator)
            except StopIteration:
                break
            if index >= self._input_cap:
                raise DiscoveryPayloadError("too many candidate seeds")
            if not isinstance(raw, CandidateSeed):
                raise DiscoveryPayloadError("invalid candidate seed")
            try:
                canonical = normalize_url(str(raw.url))
                host = AnyHttpUrl(canonical).host or ""
                if host.lower().startswith("www."):
                    canonical = normalize_url(canonical.replace(f"//{host}", f"//{host[4:]}", 1))
                identity = self._identity(canonical)
                normalized = raw.validated_replace(url=canonical)
            except (UnsafeTarget, ValidationError, ValueError):
                raise DiscoveryPayloadError("invalid candidate seed URL") from None
            grouped.setdefault(identity, []).append(normalized)
        return tuple(
            self._merge_evidence(grouped[identity]) for identity in sorted(grouped)[: self._cap]
        )

    @staticmethod
    def _merge_evidence(seeds: Sequence[CandidateSeed]) -> CandidateSeed:
        def quality(seed: CandidateSeed) -> tuple[object, ...]:
            coordinate_quality = int(seed.latitude is not None and seed.longitude is not None)
            return (
                coordinate_quality,
                int(seed.address is not None),
                int(seed.external_id is not None),
                len(seed.business_name),
                -len(str(seed.url)),
                str(seed.source_type),
            )

        best_quality = max(quality(seed) for seed in seeds)
        strongest = min(
            (seed for seed in seeds if quality(seed) == best_quality),
            key=lambda seed: (str(seed.source_url), str(seed.url)),
        )
        ordered = sorted(seeds, key=lambda seed: (quality(seed), str(seed.url)), reverse=True)
        coordinate_seeds = [
            seed for seed in ordered if seed.latitude is not None and seed.longitude is not None
        ]
        coordinates = (
            (coordinate_seeds[0].latitude, coordinate_seeds[0].longitude)
            if coordinate_seeds
            else (None, None)
        )
        earliest = min(seed.discovered_at for seed in seeds)
        observations: list[_SourceObservation] = sorted(
            (
                {
                    "source_type": str(seed.source_type),
                    "source_url": str(seed.source_url),
                    "external_id": seed.external_id,
                    "latitude": seed.latitude,
                    "longitude": seed.longitude,
                    "metadata": dict(seed.metadata),
                }
                for seed in seeds
            ),
            key=lambda item: (
                item["source_type"],
                item["source_url"],
                str(item["external_id"] or ""),
            ),
        )
        metadata: dict[str, Any] = dict(strongest.metadata)
        metadata["source_observations"] = observations
        return strongest.validated_replace(
            discovered_at=earliest,
            latitude=coordinates[0],
            longitude=coordinates[1],
            metadata=metadata,
        )

    def process(self, seeds: Iterable[CandidateSeed]) -> DiscoveryProcessResult:
        normalized = self.normalize_unique(seeds)
        if normalized and self._geofence is None:
            raise DiscoveryConfigurationError("geofence is not configured")
        outcomes: list[DiscoveryOutcome] = []
        candidates: list[object] = []
        for seed in normalized:
            geofence = (
                self._geofence.evaluate(
                    LocationEvidence(
                        latitude=seed.latitude,
                        longitude=seed.longitude,
                        address=seed.address,
                        source=seed.source_type,
                        provenance=str(seed.source_url),
                    )
                )
                if self._geofence is not None
                else None
            )
            if geofence is not None and geofence.inside is not True:
                outcomes.append(
                    DiscoveryOutcome(
                        seed,
                        "outside" if geofence.inside is False else "unresolved",
                        geofence.reason,
                        geofence=geofence,
                    )
                )
                continue
            cohort = assign_cohort(industry_hint=seed.industry_hint, name=seed.business_name).cohort
            candidate: object = seed
            status = "accepted"
            if self._repository is not None:
                persisted = self._repository.upsert_discovery_seed(seed, cohort)
                candidate = persisted.candidate
                status = (
                    "accepted" if persisted.disposition == "inserted" else persisted.disposition
                )
            if status in {"accepted", "inserted"}:
                candidates.append(candidate)
            outcomes.append(
                DiscoveryOutcome(
                    seed,
                    status,
                    geofence.reason if geofence is not None else "geofence_not_configured",
                    cohort,
                    geofence,
                    candidate,
                )
            )
        return DiscoveryProcessResult(tuple(candidates), tuple(outcomes))

    def readiness(self) -> DiscoveryReadiness:
        providers = tuple((provider.name, provider.readiness()) for provider in self._providers)
        ready = any(status == "ready" for _, status in providers)
        return DiscoveryReadiness(
            manual_sources="ready",
            automatic_discovery="ready" if ready else "not_ready",
            geofence="ready" if self._geofence is not None else "not_ready",
            providers=providers,
        )
