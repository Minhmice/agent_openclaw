"""One-request public Nominatim fallback for bounded OSM discovery."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Mapping
from typing import Any, Self

import httpx
from pydantic import ValidationError

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url
from openclaw_web.discovery.base import (
    AsyncRateLimiter,
    AsyncSleeper,
    Clock,
    DiscoveryConfigurationError,
    DiscoveryError,
    DiscoveryPayloadError,
    DiscoveryProviderError,
    DiscoveryRateLimitError,
    MonotonicClock,
    normalized_clock,
    positive_int,
    request_json,
    response_byte_limit,
    utc_now,
    validate_limit,
)
from openclaw_web.discovery.scheduler import APPROVED_COHORTS
from openclaw_web.models import CandidateSeed
from openclaw_web.settings import MarketConfig

_ENDPOINT = "https://nominatim.openstreetmap.org/search"
_MAX_RESULTS = 40
_MAX_RESPONSE_BYTES = 500_000
_USER_AGENT = "agent-openclaw-web/0.1 (OpenStreetMap Nominatim fallback)"


def _timeout(value: float) -> httpx.Timeout:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("timeout_seconds must be a real number")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 30:
        raise ValueError("timeout_seconds must be positive, finite, and at most 30")
    return httpx.Timeout(seconds)


def _endpoint(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiscoveryConfigurationError("Nominatim endpoint is not configured")
    endpoint = value.strip()
    if not endpoint.startswith("https://"):
        raise DiscoveryConfigurationError("Nominatim endpoint must use HTTPS")
    return endpoint


def _raise_status(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise DiscoveryRateLimitError("provider rate limit exceeded")
    if response.status_code >= 500:
        raise DiscoveryProviderError("provider service failed")
    if response.status_code >= 400:
        raise DiscoveryPayloadError("provider rejected discovery request")


def _coordinate(value: object, *, lower: float, upper: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or not lower <= result <= upper:
        return None
    return result


def _address(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    parts: list[str] = []
    for key in ("house_number", "road", "suburb", "city", "state"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
    return ", ".join(parts) or None


class NominatimDiscoverySource:
    """A cached, single-query fallback that only consumes public OSM website tags."""

    name = "openstreetmap-nominatim"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        endpoint: str = _ENDPOINT,
        clock: Clock = utc_now,
        monotonic: MonotonicClock = time.monotonic,
        sleeper: AsyncSleeper = asyncio.sleep,
        min_interval_seconds: float = 1.0,
        timeout_seconds: float = 15.0,
        max_results: int = _MAX_RESULTS,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
    ) -> None:
        self._endpoint = _endpoint(endpoint)
        self._clock = clock
        self._timeout = _timeout(timeout_seconds)
        self._max_results = positive_int(max_results, field="max_results", cap=_MAX_RESULTS)
        self._max_response_bytes = response_byte_limit(max_response_bytes)
        self._limiter = AsyncRateLimiter(
            min_interval_seconds, monotonic=monotonic, sleeper=sleeper
        )
        self._client = client if client is not None else httpx.AsyncClient()
        self._owns_client = client is None
        self._cache_lock = asyncio.Lock()
        self._cache: tuple[CandidateSeed, ...] | None = None
        self._failure: DiscoveryError | None = None

    def readiness(self) -> str:
        return "ready"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(ready=True)"

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    @staticmethod
    def _query(market: MarketConfig, cohort: str) -> str:
        if cohort not in APPROVED_COHORTS:
            raise ValueError("cohort must be approved")
        return f"company, {market.center.name}"

    async def _request(self, market: MarketConfig) -> Any:
        await self._limiter.wait()
        return await request_json(
            self._client,
            "GET",
            self._endpoint,
            headers={
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
            payload=None,
            params={
                "q": self._query(market, "other"),
                "format": "jsonv2",
                "limit": self._max_results,
                "extratags": 1,
                "addressdetails": 1,
            },
            timeout=self._timeout,
            max_response_bytes=self._max_response_bytes,
            status_handler=_raise_status,
        )

    def _seed(self, item: Mapping[str, object]) -> CandidateSeed | None:
        osm_type = item.get("osm_type")
        osm_id = item.get("osm_id")
        if (
            not isinstance(osm_type, str)
            or osm_type not in {"node", "way", "relation"}
            or isinstance(osm_id, bool)
            or not isinstance(osm_id, int)
            or osm_id <= 0
        ):
            return None
        tags = item.get("extratags")
        if not isinstance(tags, Mapping):
            return None
        website = tags.get("website") or tags.get("contact:website")
        if not isinstance(website, str) or not website.strip():
            return None
        display_name = item.get("display_name")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            if not isinstance(display_name, str) or not display_name.strip():
                return None
            name = display_name.split(",", 1)[0].strip()
        latitude = _coordinate(item.get("lat"), lower=-90.0, upper=90.0)
        longitude = _coordinate(item.get("lon"), lower=-180.0, upper=180.0)
        if latitude is None or longitude is None:
            return None
        external_id = f"{osm_type}/{osm_id}"
        try:
            return CandidateSeed.model_validate(
                {
                    "url": normalize_url(website),
                    "business_name": name.strip(),
                    "source_url": f"https://www.openstreetmap.org/{external_id}",
                    "source_type": self.name,
                    "discovered_at": normalized_clock(self._clock),
                    "address": _address(item.get("address")),
                    "latitude": latitude,
                    "longitude": longitude,
                    "industry_hint": "other",
                    "external_id": external_id,
                    "metadata": {
                        "provider": self.name,
                        "osm_type": osm_type,
                        "osm_id": osm_id,
                    },
                }
            )
        except (UnsafeTarget, ValidationError, ValueError):
            return None

    def _parse(self, payload: Any) -> tuple[CandidateSeed, ...]:
        if not isinstance(payload, list) or len(payload) > _MAX_RESULTS:
            raise DiscoveryPayloadError("invalid provider payload")
        results: list[CandidateSeed] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, Mapping):
                raise DiscoveryPayloadError("invalid provider payload")
            seed = self._seed(item)
            if seed is None or str(seed.url) in seen:
                continue
            seen.add(str(seed.url))
            results.append(seed)
        return tuple(results)

    async def _results(self, market: MarketConfig) -> tuple[CandidateSeed, ...]:
        async with self._cache_lock:
            if self._failure is not None:
                raise self._failure
            if self._cache is None:
                try:
                    self._cache = self._parse(await self._request(market))
                except DiscoveryError as error:
                    self._failure = error
                    raise
            return self._cache

    async def discover(
        self, market: MarketConfig, cohort: str, limit: int
    ) -> tuple[CandidateSeed, ...]:
        requested = validate_limit(limit, cap=_MAX_RESULTS)
        if cohort != "other":
            if cohort not in APPROVED_COHORTS:
                raise ValueError("cohort must be approved")
            return ()
        return (await self._results(market))[:requested]


class NominatimDiscoveryProvider(NominatimDiscoverySource):
    """Compatibility wrapper with an explicit client-first constructor."""

    def __init__(self, client: httpx.AsyncClient, **kwargs: Any) -> None:
        super().__init__(client=client, **kwargs)
