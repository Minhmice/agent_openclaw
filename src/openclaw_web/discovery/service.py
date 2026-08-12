"""Candidate normalization, geofence/cohort processing, and discovery readiness."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import tldextract
from pydantic import AnyHttpUrl, ValidationError

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url
from openclaw_web.discovery.base import (
    AutomaticDiscoveryProvider,
    DiscoveryConfigurationError,
    DiscoveryPayloadError,
)
from openclaw_web.discovery.scheduler import assign_cohort
from openclaw_web.geofence import GeofenceResult, GeofenceService, LocationEvidence
from openclaw_web.models import Candidate, CandidateSeed

_EXTRACTOR = tldextract.TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
    fallback_to_snapshot=True,
    include_psl_private_domains=True,
)


class CandidateRepository(Protocol):
    def find_duplicate(self, candidate: CandidateSeed) -> Candidate | None: ...

    def upsert_candidate(self, url: str, business_name: str, address: str | None) -> object: ...


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
        results: list[CandidateSeed] = []
        seen: set[str] = set()
        for raw in seeds:
            if len(results) >= self._cap:
                break
            if not isinstance(raw, CandidateSeed):
                raise DiscoveryPayloadError("invalid candidate seed")
            try:
                canonical = normalize_url(str(raw.url))
                host = AnyHttpUrl(canonical).host or ""
                if host.lower().startswith("www."):
                    canonical = normalize_url(canonical.replace(f"//{host}", f"//{host[4:]}", 1))
                identity = self._identity(canonical)
                if identity in seen:
                    continue
                normalized = raw.validated_replace(url=canonical)
            except (UnsafeTarget, ValidationError, ValueError):
                raise DiscoveryPayloadError("invalid candidate seed URL") from None
            seen.add(identity)
            results.append(normalized)
        return tuple(results)

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
                duplicate = self._repository.find_duplicate(seed)
                candidate = self._repository.upsert_candidate(
                    str(seed.url), seed.business_name, seed.address
                )
                if duplicate is not None:
                    status = "duplicate"
            if status == "accepted":
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
