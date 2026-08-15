"""Bounded Google Places API v1 text-search discovery adapter."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Mapping
from typing import Any, Self
from urllib.parse import quote_plus

import httpx
from pydantic import SecretStr, ValidationError

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url
from openclaw_web.discovery.base import (
    AsyncRateLimiter,
    AsyncSleeper,
    Clock,
    DiscoveryConfigurationError,
    DiscoveryPayloadError,
    MonotonicClock,
    normalized_clock,
    positive_int,
    request_json,
    response_byte_limit,
    utc_now,
    validate_limit,
)
from openclaw_web.discovery.scheduler import APPROVED_COHORTS
from openclaw_web.discovery.serper import _key, _raise_status
from openclaw_web.models import CandidateSeed
from openclaw_web.settings import MarketConfig

_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.websiteUri,places.googleMapsUri,nextPageToken"
)
_MAX_RESULTS = 200
_TERMS = {
    "manufacturer": "nhà sản xuất",
    "professional-services": "dịch vụ chuyên nghiệp",
    "local-service": "dịch vụ địa phương",
    "showroom-retail": "showroom bán lẻ",
    "ecommerce": "thương mại điện tử",
    "education": "giáo dục",
    "healthcare": "y tế",
    "hospitality": "khách sạn nhà hàng",
    "real-estate": "bất động sản",
    "other": "doanh nghiệp",
}


class GooglePlacesDiscoverySource:
    name = "google-places"

    def __init__(
        self,
        api_key: str | SecretStr,
        client: httpx.AsyncClient | None = None,
        *,
        clock: Clock = utc_now,
        monotonic: MonotonicClock = time.monotonic,
        sleeper: AsyncSleeper = asyncio.sleep,
        min_interval_seconds: float = 0,
        timeout_seconds: float = 10,
        page_size: int = 20,
        max_pages: int = 5,
        max_requests: int = 100,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        self._api_key = _key(api_key)
        self._clock = clock
        self._timeout = httpx.Timeout(timeout_seconds)
        self._page_size = positive_int(page_size, field="page_size", cap=20)
        self._max_pages = positive_int(max_pages, field="max_pages", cap=20)
        self._max_requests = positive_int(max_requests, field="max_requests", cap=400)
        self._max_response_bytes = response_byte_limit(max_response_bytes)
        self._limiter = AsyncRateLimiter(min_interval_seconds, monotonic=monotonic, sleeper=sleeper)
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
    def _destination(
        latitude: float, longitude: float, distance_km: float, bearing_degrees: float
    ) -> tuple[float, float]:
        earth_radius_km = 6371.0088
        lat = math.radians(latitude)
        lon = math.radians(longitude)
        angular = distance_km / earth_radius_km
        bearing = math.radians(bearing_degrees)
        target_lat = math.asin(
            math.sin(lat) * math.cos(angular)
            + math.cos(lat) * math.sin(angular) * math.cos(bearing)
        )
        target_lon = lon + math.atan2(
            math.sin(bearing) * math.sin(angular) * math.cos(lat),
            math.cos(angular) - math.sin(lat) * math.sin(target_lat),
        )
        return math.degrees(target_lat), ((math.degrees(target_lon) + 180) % 360) - 180

    @classmethod
    def _search_circles(cls, market: MarketConfig) -> tuple[tuple[float, float, float], ...]:
        radius = market.radius_km
        if radius <= 50:
            return ((market.center.latitude, market.center.longitude, radius),)
        if radius > 80:
            raise DiscoveryConfigurationError("market radius cannot be covered by bounded searches")
        # Six 50 km circles on a radius/2 ring cover every radial segment of a
        # market up to 80 km.  The angular worst case is midway between adjacent
        # centers (30 degrees), where the outer-boundary distance stays < 50 km.
        ring_distance = radius / 2
        circles = [(market.center.latitude, market.center.longitude, 50.0)]
        for bearing in range(0, 360, 60):
            latitude, longitude = cls._destination(
                market.center.latitude, market.center.longitude, ring_distance, bearing
            )
            circles.append((latitude, longitude, 50.0))
        return tuple(circles)

    async def _request(self, payload: Mapping[str, object]) -> Any:
        await self._limiter.wait()
        return await request_json(
            self._client,
            "POST",
            _ENDPOINT,
            headers={
                "X-Goog-Api-Key": self._api_key.get_secret_value(),
                "X-Goog-FieldMask": _FIELD_MASK,
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=self._timeout,
            max_response_bytes=self._max_response_bytes,
            status_handler=lambda response: _raise_status(response, clock=self._clock),
        )

    async def discover(
        self, market: MarketConfig, cohort: str, limit: int
    ) -> tuple[CandidateSeed, ...]:
        requested = validate_limit(limit, cap=_MAX_RESULTS)
        if cohort not in APPROVED_COHORTS:
            raise ValueError("cohort must be approved")
        query = f'{_TERMS[cohort]} "{market.center.name}"'
        circles = self._search_circles(market)
        if len(circles) > self._max_requests:
            raise DiscoveryConfigurationError("request budget cannot cover the configured market")
        results: list[CandidateSeed] = []
        tokens: list[str | None] = [None] * len(circles)
        seen_tokens: list[set[str]] = [set() for _ in circles]
        active = [True] * len(circles)
        requests = 0
        for page in range(self._max_pages):
            for center_index, (latitude, longitude, radius_km) in enumerate(circles):
                if not active[center_index] or requests >= self._max_requests:
                    continue
                token = tokens[center_index]
                body: dict[str, object] = {
                    "textQuery": query,
                    "pageSize": self._page_size,
                    "locationBias": {
                        "circle": {
                            "center": {
                                "latitude": latitude,
                                "longitude": longitude,
                            },
                            "radius": radius_km * 1000,
                        }
                    },
                }
                if token is not None:
                    body["pageToken"] = token
                payload = await self._request(body)
                requests += 1
                if not isinstance(payload, dict) or set(payload) - {"places", "nextPageToken"}:
                    raise DiscoveryPayloadError("invalid provider payload")
                places = payload.get("places", [])
                if not isinstance(places, list) or len(places) > 20:
                    raise DiscoveryPayloadError("invalid provider payload")
                for place in places:
                    seed = self._seed(place, cohort, query, center_index, page, token)
                    if seed is not None:
                        results.append(seed)
                next_token = payload.get("nextPageToken")
                if not isinstance(next_token, str) or not next_token.strip():
                    active[center_index] = False
                    continue
                normalized_token = next_token.strip()
                if normalized_token in seen_tokens[center_index]:
                    active[center_index] = False
                    continue
                seen_tokens[center_index].add(normalized_token)
                tokens[center_index] = normalized_token
            if requests >= self._max_requests or not any(active):
                break

        selected: list[CandidateSeed] = []
        seen_urls: set[str] = set()
        seen_places: set[str] = set()
        for seed in results:
            canonical = str(seed.url)
            if canonical in seen_urls or (
                seed.external_id is not None and seed.external_id in seen_places
            ):
                continue
            seen_urls.add(canonical)
            if seed.external_id is not None:
                seen_places.add(seed.external_id)
            selected.append(seed)
            if len(selected) >= requested:
                break
        return tuple(selected)

    def _seed(
        self,
        place: object,
        cohort: str,
        query: str,
        center_index: int,
        page: int,
        page_token: str | None,
    ) -> CandidateSeed | None:
        if not isinstance(place, dict):
            return None
        name = place.get("displayName")
        name_text = name.get("text") if isinstance(name, dict) else None
        website = place.get("websiteUri")
        location = place.get("location")
        if (
            not isinstance(name_text, str)
            or not name_text.strip()
            or not isinstance(website, str)
            or not isinstance(location, dict)
        ):
            return None
        latitude, longitude = location.get("latitude"), location.get("longitude")
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, int | float)
            or not isinstance(longitude, int | float)
        ):
            return None
        maps = place.get("googleMapsUri")
        place_id = place.get("id")
        source = (
            maps
            if isinstance(maps, str) and maps.startswith("https://")
            else f"https://www.google.com/maps/search/?api=1&query={quote_plus(name_text)}"
        )
        try:
            return CandidateSeed.model_validate(
                {
                    "url": normalize_url(website),
                    "business_name": name_text.strip(),
                    "source_url": source,
                    "source_type": "google-places",
                    "discovered_at": normalized_clock(self._clock),
                    "address": place.get("formattedAddress")
                    if isinstance(place.get("formattedAddress"), str)
                    else None,
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                    "industry_hint": cohort,
                    "external_id": place_id
                    if isinstance(place_id, str) and place_id.strip()
                    else None,
                    "metadata": {
                        "provider": "google-places",
                        "query": query[:200],
                        "search_center": center_index,
                        "page": page + 1,
                        "page_token": page_token,
                    },
                }
            )
        except (UnsafeTarget, ValidationError, ValueError):
            return None


class GooglePlacesDiscoveryProvider(GooglePlacesDiscoverySource):
    """Compatibility wrapper for the original client-first constructor."""

    def __init__(self, client: httpx.AsyncClient, api_key: str | SecretStr, **kwargs: Any) -> None:
        super().__init__(api_key=api_key, client=client, **kwargs)
