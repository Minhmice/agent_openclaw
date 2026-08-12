"""Bounded Google Places API v1 text-search discovery adapter."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote_plus

import httpx
from pydantic import SecretStr, ValidationError

from openclaw_web.crawl.safety import UnsafeTarget, normalize_url
from openclaw_web.discovery.base import (
    AsyncRateLimiter,
    AsyncSleeper,
    Clock,
    DiscoveryPayloadError,
    DiscoveryProviderError,
    MonotonicClock,
    normalized_clock,
    positive_int,
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


class GooglePlacesDiscoveryProvider:
    name = "google-places"

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str | SecretStr,
        *,
        clock: Clock = utc_now,
        monotonic: MonotonicClock = time.monotonic,
        sleeper: AsyncSleeper = asyncio.sleep,
        min_interval_seconds: float = 0,
        timeout_seconds: float = 10,
        page_size: int = 20,
        max_pages: int = 5,
    ) -> None:
        self._client = client
        self._api_key = _key(api_key)
        self._clock = clock
        self._timeout = httpx.Timeout(timeout_seconds)
        self._page_size = positive_int(page_size, field="page_size", cap=20)
        self._max_pages = positive_int(max_pages, field="max_pages", cap=20)
        self._limiter = AsyncRateLimiter(min_interval_seconds, monotonic=monotonic, sleeper=sleeper)

    def readiness(self) -> str:
        return "ready"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(ready=True)"

    async def _request(self, payload: Mapping[str, object]) -> Any:
        await self._limiter.wait()
        try:
            response = await self._client.post(
                _ENDPOINT,
                headers={
                    "X-Goog-Api-Key": self._api_key.get_secret_value(),
                    "X-Goog-FieldMask": _FIELD_MASK,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            raise DiscoveryProviderError("provider request failed") from None
        _raise_status(response)
        try:
            return response.json()
        except ValueError:
            raise DiscoveryPayloadError("invalid provider payload") from None

    async def discover(
        self, market: MarketConfig, cohort: str, *, limit: int
    ) -> tuple[CandidateSeed, ...]:
        requested = validate_limit(limit, cap=_MAX_RESULTS)
        if cohort not in APPROVED_COHORTS:
            raise ValueError("cohort must be approved")
        query = f'{_TERMS[cohort]} "{market.center.name}"'
        token: str | None = None
        seen_tokens: set[str] = set()
        seen_urls: set[str] = set()
        results: list[CandidateSeed] = []
        for _page in range(self._max_pages):
            body: dict[str, object] = {
                "textQuery": query,
                "pageSize": self._page_size,
                "locationRestriction": {
                    "circle": {
                        "center": {
                            "latitude": market.center.latitude,
                            "longitude": market.center.longitude,
                        },
                        "radius": min(market.radius_km * 1000, 50_000.0),
                    }
                },
            }
            if token is not None:
                body["pageToken"] = token
            payload = await self._request(body)
            if not isinstance(payload, dict) or set(payload) - {"places", "nextPageToken"}:
                raise DiscoveryPayloadError("invalid provider payload")
            places = payload.get("places", [])
            if not isinstance(places, list) or len(places) > 20:
                raise DiscoveryPayloadError("invalid provider payload")
            for place in places:
                seed = self._seed(place, cohort, query)
                if seed is None:
                    continue
                canonical = str(seed.url)
                if canonical in seen_urls:
                    continue
                seen_urls.add(canonical)
                results.append(seed)
                if len(results) >= requested:
                    return tuple(results)
            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token.strip():
                break
            token = next_token.strip()
            if token in seen_tokens:
                break
            seen_tokens.add(token)
        return tuple(results)

    def _seed(self, place: object, cohort: str, query: str) -> CandidateSeed | None:
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
                    "metadata": {"provider": "google-places", "query": query[:200]},
                }
            )
        except (UnsafeTarget, ValidationError, ValueError):
            return None
