"""Bounded asynchronous adapter for the Serper Google Search API."""

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
    DiscoveryConfigurationError,
    DiscoveryPayloadError,
    DiscoveryProviderError,
    DiscoveryRateLimitError,
    MonotonicClock,
    normalized_clock,
    positive_int,
    utc_now,
    validate_limit,
)
from openclaw_web.discovery.scheduler import APPROVED_COHORTS
from openclaw_web.models import CandidateSeed
from openclaw_web.settings import MarketConfig

_ENDPOINT = "https://google.serper.dev/search"
_MAX_RESULTS = 200
_TERMS = {
    "manufacturer": "nhà sản xuất",
    "professional-services": "dịch vụ chuyên nghiệp",
    "local-service": "dịch vụ địa phương",
    "showroom-retail": "showroom cửa hàng bán lẻ",
    "ecommerce": "thương mại điện tử",
    "education": "giáo dục đào tạo",
    "healthcare": "y tế phòng khám",
    "hospitality": "khách sạn nhà hàng",
    "real-estate": "bất động sản",
    "other": "doanh nghiệp",
}


def _key(value: str | SecretStr) -> SecretStr:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not isinstance(raw, str) or not raw.strip():
        raise DiscoveryConfigurationError("provider API credential is not configured")
    return SecretStr(raw.strip())


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if 0 <= result <= 3600 else None


def _raise_status(response: httpx.Response) -> None:
    status = response.status_code
    if status in (401, 403):
        raise DiscoveryConfigurationError("provider authentication failed")
    if status == 429:
        raise DiscoveryRateLimitError(
            "provider rate limit exceeded",
            retry_after=_retry_after(response.headers.get("Retry-After")),
        )
    if 500 <= status:
        raise DiscoveryProviderError("provider service failed")
    if 400 <= status:
        raise DiscoveryPayloadError("provider rejected discovery request")


class SerperDiscoveryProvider:
    name = "serper"

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
        page_size: int = 10,
        max_pages: int = 5,
    ) -> None:
        self._client = client
        self._api_key = _key(api_key)
        self._clock = clock
        self._timeout = httpx.Timeout(timeout_seconds)
        self._page_size = positive_int(page_size, field="page_size", cap=100)
        self._max_pages = positive_int(max_pages, field="max_pages", cap=20)
        self._limiter = AsyncRateLimiter(min_interval_seconds, monotonic=monotonic, sleeper=sleeper)

    def readiness(self) -> str:
        return "ready"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(ready=True)"

    @staticmethod
    def _query(market: MarketConfig, cohort: str) -> str:
        if cohort not in APPROVED_COHORTS:
            raise ValueError("cohort must be approved")
        return f'{_TERMS[cohort]} "{market.center.name}" website'

    async def _request(self, payload: Mapping[str, object]) -> Any:
        await self._limiter.wait()
        try:
            response = await self._client.post(
                _ENDPOINT,
                headers={
                    "X-API-KEY": self._api_key.get_secret_value(),
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
        results: list[CandidateSeed] = []
        seen: set[str] = set()
        for page in range(1, self._max_pages + 1):
            payload = await self._request(
                {"q": self._query(market, cohort), "num": self._page_size, "page": page}
            )
            if not isinstance(payload, dict) or set(payload) - {
                "organic",
                "searchParameters",
                "credits",
            }:
                raise DiscoveryPayloadError("invalid provider payload")
            organic = payload.get("organic", [])
            if not isinstance(organic, list) or len(organic) > 100:
                raise DiscoveryPayloadError("invalid provider payload")
            valid_on_page = 0
            for entry in organic:
                if not isinstance(entry, dict):
                    continue
                title, link = entry.get("title"), entry.get("link")
                if not isinstance(title, str) or not title.strip() or not isinstance(link, str):
                    continue
                try:
                    canonical = normalize_url(link)
                    if canonical in seen:
                        continue
                    seed = CandidateSeed.model_validate(
                        {
                            "url": canonical,
                            "business_name": title.strip(),
                            "source_url": (
                                "https://www.google.com/search?q="
                                f"{quote_plus(self._query(market, cohort))}&start={(page - 1) * self._page_size}"
                            ),
                            "source_type": "serper",
                            "discovered_at": normalized_clock(self._clock),
                            "industry_hint": cohort,
                            "metadata": {"provider": "serper", "page": page},
                        }
                    )
                except (UnsafeTarget, ValidationError, ValueError):
                    continue
                seen.add(canonical)
                valid_on_page += 1
                results.append(seed)
                if len(results) >= requested:
                    return tuple(results)
            if not organic or valid_on_page == 0 and page > 1:
                break
        return tuple(results)
