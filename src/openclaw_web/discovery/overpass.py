"""Bounded public OpenStreetMap discovery through the Overpass API."""

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
    _strict_json_bytes,
    normalized_clock,
    response_byte_limit,
    utc_now,
    validate_limit,
)
from openclaw_web.discovery.scheduler import APPROVED_COHORTS
from openclaw_web.models import CandidateSeed
from openclaw_web.settings import MarketConfig

_ENDPOINT = "https://overpass-api.de/api/interpreter"
_MAX_RESULTS = 200
_MAX_RADIUS_KM = 80.0

# Each cohort is intentionally expressed as a finite set of exact OSM tag
# selectors. The public source never runs an unbounded or wildcard tag scan.
_COHORT_SELECTORS: Mapping[str, tuple[tuple[str, str | None], ...]] = {
    "manufacturer": (("industrial", "factory|manufacture|warehouse"), ("craft", None)),
    "professional-services": (
        ("office", "accountant|architect|consulting|financial|insurance|lawyer|notary"),
    ),
    "local-service": (
        ("craft", None),
        ("shop", "car_repair|dry_cleaning|hairdresser|laundry|repair"),
    ),
    "showroom-retail": (("shop", None),),
    "ecommerce": (("shop", "department_store|general|mall|wholesale"),),
    "education": (("amenity", "college|kindergarten|language_school|school|university"),),
    "healthcare": (
        ("amenity", "clinic|dentist|doctors|hospital|pharmacy"),
        ("healthcare", "clinic|dentist|doctor|hospital|pharmacy"),
    ),
    "hospitality": (
        ("amenity", "bar|cafe|fast_food|food_court|restaurant"),
        ("tourism", "guest_house|hostel|hotel|motel|resort"),
    ),
    "real-estate": (("office", "estate_agent|property_management"),),
    "other": (
        ("amenity", "bank|cinema|community_centre|marketplace|theatre"),
        ("office", "company|government|ngo|telecommunication"),
        ("shop", "department_store|general|mall|supermarket|wholesale"),
    ),
}


def _timeout(value: float) -> httpx.Timeout:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("timeout_seconds must be a real number")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 30:
        raise ValueError("timeout_seconds must be positive, finite, and at most 30")
    return httpx.Timeout(seconds)


def _raise_status(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise DiscoveryRateLimitError("provider rate limit exceeded")
    if response.status_code >= 500:
        raise DiscoveryProviderError("provider service failed")
    if response.status_code >= 400:
        raise DiscoveryPayloadError("provider rejected discovery request")


class OverpassDiscoverySource:
    """One-request deterministic OSM discovery source without credentials."""

    name = "openstreetmap-overpass"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        endpoint: str = _ENDPOINT,
        clock: Clock = utc_now,
        monotonic: MonotonicClock = time.monotonic,
        sleeper: AsyncSleeper = asyncio.sleep,
        min_interval_seconds: float = 1.0,
        timeout_seconds: float = 25.0,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        self._endpoint = endpoint
        self._clock = clock
        self._timeout = _timeout(timeout_seconds)
        self._max_response_bytes = response_byte_limit(max_response_bytes)
        self._limiter = AsyncRateLimiter(
            min_interval_seconds, monotonic=monotonic, sleeper=sleeper
        )
        self._client = client if client is not None else httpx.AsyncClient()
        self._owns_client = client is None

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
        if cohort not in APPROVED_COHORTS or cohort not in _COHORT_SELECTORS:
            raise ValueError("cohort must be approved")
        if market.radius_km > _MAX_RADIUS_KM:
            raise DiscoveryConfigurationError("Overpass market radius must not exceed 80 km")
        radius_m = round(market.radius_km * 1000)
        around = f"around:{radius_m},{market.center.latitude},{market.center.longitude}"
        statements: list[str] = []
        for key, values in _COHORT_SELECTORS[cohort]:
            selector = f'["{key}"]' if values is None else f'["{key}"~"^({values})$"]'
            for website_tag in ("website", "contact:website"):
                statements.append(
                    f'nwr({around})["name"]["{website_tag}"]{selector};'
                )
        return "[out:json][timeout:25];(" + "".join(statements) + ");out center 200;"

    async def _request(self, query: str) -> Any:
        await self._limiter.wait()
        try:
            async with self._client.stream(
                "POST",
                self._endpoint,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "agent-openclaw-web/0.1 (public OSM discovery)",
                },
                data={"data": query},
                timeout=self._timeout,
            ) as response:
                _raise_status(response)
                media_type = (
                    response.headers.get("Content-Type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if media_type != "application/json" and not media_type.endswith("+json"):
                    raise DiscoveryPayloadError("invalid provider payload")
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        declared_size = int(declared, 10)
                    except ValueError:
                        raise DiscoveryPayloadError("invalid provider payload") from None
                    if declared_size < 0 or declared_size > self._max_response_bytes:
                        raise DiscoveryPayloadError("invalid provider payload")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    remaining = self._max_response_bytes + 1 - len(body)
                    if remaining <= 0:
                        raise DiscoveryPayloadError("invalid provider payload")
                    body.extend(chunk[:remaining])
                    if len(body) > self._max_response_bytes or len(chunk) > remaining:
                        raise DiscoveryPayloadError("invalid provider payload")
                return _strict_json_bytes(bytes(body))
        except DiscoveryError:
            raise
        except httpx.HTTPError:
            raise DiscoveryProviderError("provider request failed") from None

    async def discover(
        self, market: MarketConfig, cohort: str, limit: int
    ) -> tuple[CandidateSeed, ...]:
        requested = validate_limit(limit, cap=_MAX_RESULTS)
        payload = await self._request(self._query(market, cohort))
        if not isinstance(payload, dict):
            raise DiscoveryPayloadError("invalid provider payload")
        elements = payload.get("elements")
        if not isinstance(elements, list) or len(elements) > _MAX_RESULTS:
            raise DiscoveryPayloadError("invalid provider payload")

        results: list[CandidateSeed] = []
        seen_urls: set[str] = set()
        seen_osm: set[str] = set()
        for element in elements:
            if not isinstance(element, dict):
                raise DiscoveryPayloadError("invalid provider payload")
            seed = self._seed(element, cohort)
            if seed is None:
                continue
            url = str(seed.url)
            osm_id = seed.external_id
            if url in seen_urls or osm_id is None or osm_id in seen_osm:
                continue
            seen_urls.add(url)
            seen_osm.add(osm_id)
            results.append(seed)
            if len(results) >= requested:
                break
        return tuple(results)

    def _seed(self, element: dict[str, object], cohort: str) -> CandidateSeed | None:
        osm_type = element.get("type")
        osm_id = element.get("id")
        tags = element.get("tags")
        if (
            osm_type not in {"node", "way", "relation"}
            or isinstance(osm_id, bool)
            or not isinstance(osm_id, int)
            or osm_id <= 0
            or not isinstance(tags, dict)
        ):
            return None
        name = tags.get("name")
        website = tags.get("website") or tags.get("contact:website")
        if not isinstance(name, str) or not name.strip() or not isinstance(website, str):
            return None

        location = element if osm_type == "node" else element.get("center")
        if not isinstance(location, dict):
            return None
        latitude, longitude = location.get("lat"), location.get("lon")
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, int | float)
            or not isinstance(longitude, int | float)
        ):
            return None
        address = self._address(tags)
        external_id = f"{osm_type}/{osm_id}"
        try:
            return CandidateSeed.model_validate(
                {
                    "url": normalize_url(website),
                    "business_name": name.strip(),
                    "source_url": f"https://www.openstreetmap.org/{external_id}",
                    "source_type": self.name,
                    "discovered_at": normalized_clock(self._clock),
                    "address": address,
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                    "industry_hint": cohort,
                    "external_id": external_id,
                    "metadata": {"osm_type": osm_type, "osm_id": osm_id},
                }
            )
        except (UnsafeTarget, ValidationError, ValueError):
            return None

    @staticmethod
    def _address(tags: dict[object, object]) -> str | None:
        full = tags.get("addr:full")
        if isinstance(full, str) and full.strip():
            return full.strip()
        parts = []
        for key in ("addr:housenumber", "addr:street", "addr:suburb", "addr:city"):
            value = tags.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        return ", ".join(parts) or None


class OverpassDiscoveryProvider(OverpassDiscoverySource):
    """Compatibility wrapper with an explicit client-first constructor."""

    def __init__(self, client: httpx.AsyncClient, **kwargs: Any) -> None:
        super().__init__(client=client, **kwargs)
