from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from openclaw_web.discovery import (
    APPROVED_COHORTS,
    DiscoveryPayloadError,
    DiscoveryProviderError,
    DiscoveryRateLimitError,
    NominatimDiscoverySource,
)
from openclaw_web.settings import MarketCenter, MarketConfig

NOW = datetime(2026, 8, 26, tzinfo=UTC)
MARKET = MarketConfig(
    market_id="hanoi-80km",
    center=MarketCenter(name="Hanoi", latitude=21.0285, longitude=105.8542),
    radius_km=80.0,
    industries=["*"],
    timezone="Asia/Bangkok",
)


def _clock() -> datetime:
    return NOW


def _json_response(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


@pytest.mark.asyncio
async def test_nominatim_queries_once_and_returns_only_public_website_seeds() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.params["q"] == "company, Hanoi"
        assert request.url.params["format"] == "jsonv2"
        assert request.url.params["extratags"] == "1"
        assert request.url.params["limit"] == "40"
        return _json_response(
            [
                {
                    "osm_type": "node",
                    "osm_id": 42,
                    "display_name": "Example Company, Hanoi",
                    "lat": "21.0285",
                    "lon": "105.8542",
                    "address": {"road": "Main Street", "city": "Hanoi"},
                    "extratags": {"website": "HTTPS://WWW.Example.COM"},
                },
                {
                    "osm_type": "way",
                    "osm_id": 43,
                    "display_name": "Contact Company, Hanoi",
                    "lat": "21.0300",
                    "lon": "105.8500",
                    "address": {"city": "Hanoi"},
                    "extratags": {"contact:website": "https://contact.example.com"},
                },
                {
                    "osm_type": "node",
                    "osm_id": 44,
                    "display_name": "No Website, Hanoi",
                    "lat": "21.0300",
                    "lon": "105.8500",
                    "address": {"city": "Hanoi"},
                    "extratags": {},
                },
            ]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = NominatimDiscoverySource(client=client, clock=_clock, min_interval_seconds=0)
        results = await asyncio.gather(
            *(source.discover(MARKET, cohort, limit=10) for cohort in APPROVED_COHORTS)
        )

    assert len(requests) == 1
    assert all(not results[index] for index, cohort in enumerate(APPROVED_COHORTS) if cohort != "other")
    seeds = results[APPROVED_COHORTS.index("other")]
    assert [seed.business_name for seed in seeds] == ["Example Company", "Contact Company"]
    assert [str(seed.url) for seed in seeds] == [
        "https://www.example.com/",
        "https://contact.example.com/",
    ]
    assert seeds[0].source_type == "openstreetmap-nominatim"
    assert str(seeds[0].source_url) == "https://www.openstreetmap.org/node/42"
    assert seeds[0].external_id == "node/42"
    assert seeds[0].industry_hint == "other"
    assert seeds[0].address == "Main Street, Hanoi"
    assert seeds[0].discovered_at == NOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (429, DiscoveryRateLimitError),
        (503, DiscoveryProviderError),
        (200, DiscoveryPayloadError),
    ],
)
async def test_nominatim_rejects_provider_failures_and_invalid_payloads(
    status: int, error: type[Exception]
) -> None:
    response = (
        httpx.Response(status, json={"not": "a list"})
        if status == 200
        else _json_response([], status)
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    ) as client:
        source = NominatimDiscoverySource(client=client, clock=_clock, min_interval_seconds=0)
        with pytest.raises(error):
            await source.discover(MARKET, "other", limit=1)
