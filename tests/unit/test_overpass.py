from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from openclaw_web.discovery import (
    APPROVED_COHORTS,
    DiscoveryConfigurationError,
    DiscoveryPayloadError,
    DiscoveryProviderError,
    DiscoveryRateLimitError,
    OverpassDiscoveryProvider,
    OverpassDiscoverySource,
)
from openclaw_web.settings import MarketCenter, MarketConfig

NOW = datetime(2026, 8, 13, tzinfo=UTC)
MARKET = MarketConfig(
    market_id="hanoi",
    center=MarketCenter(name="Hà Nội", latitude=21.0278, longitude=105.8342),
    radius_km=80.0,
    industries=["manufacturing"],
    timezone="Asia/Bangkok",
)


def _clock() -> datetime:
    return NOW


def _json_response(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


@pytest.mark.asyncio
async def test_overpass_parses_osm_evidence_and_uses_one_bounded_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _json_response(
            {
                "version": 0.6,
                "generator": "Overpass API",
                "osm3s": {"timestamp_osm_base": "2026-08-13T00:00:00Z"},
                "elements": [
                    {
                        "type": "way",
                        "id": 42,
                        "center": {"lat": 21.03, "lon": 105.84},
                        "tags": {
                            "name": "Nhà máy Việt",
                            "website": "HTTPS://WWW.Example.COM",
                            "addr:housenumber": "12",
                            "addr:street": "Trần Hưng Đạo",
                            "addr:city": "Hà Nội",
                        },
                    }
                ],
            }
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        seeds = await OverpassDiscoveryProvider(client, clock=_clock).discover(
            MARKET, "manufacturer", limit=20
        )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url == "https://overpass-api.de/api/interpreter"
    query = parse_qs(request.content.decode())["data"][0]
    assert "around:80000,21.0278,105.8342" in query
    assert '["name"]["website"]' in query
    assert '["name"]["contact:website"]' in query
    assert "out center 200" in query
    assert "[timeout:30]" not in query
    assert len(seeds) == 1
    seed = seeds[0]
    assert str(seed.url) == "https://www.example.com/"
    assert seed.business_name == "Nhà máy Việt"
    assert str(seed.source_url) == "https://www.openstreetmap.org/way/42"
    assert seed.source_type == "openstreetmap-overpass"
    assert seed.discovered_at == NOW
    assert seed.address == "12, Trần Hưng Đạo, Hà Nội"
    assert seed.latitude == 21.03
    assert seed.longitude == 105.84
    assert seed.external_id == "way/42"
    assert seed.metadata == {"osm_type": "way", "osm_id": 42}


@pytest.mark.asyncio
@pytest.mark.parametrize("cohort", APPROVED_COHORTS)
async def test_overpass_maps_every_approved_cohort_to_finite_osm_tags(cohort: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _json_response({"elements": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await OverpassDiscoverySource(client=client).discover(MARKET, cohort, limit=200)

    assert len(requests) == 1
    query = parse_qs(requests[0].content.decode())["data"][0]
    assert "nwr(around:" in query
    assert "~\".*\"" not in query
    assert "[\"name\"]" in query
    assert query.count("out center 200") == 1


@pytest.mark.asyncio
async def test_overpass_uses_contact_website_and_node_coordinates() -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 7,
                "lat": 21.0,
                "lon": 105.8,
                "tags": {"name": "Phòng khám", "contact:website": "https://clinic.example.com"},
            }
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _json_response(payload))
    ) as client:
        seeds = await OverpassDiscoverySource(client=client).discover(
            MARKET, "healthcare", limit=1
        )

    assert str(seeds[0].url) == "https://clinic.example.com/"
    assert seeds[0].latitude == 21.0
    assert seeds[0].longitude == 105.8


@pytest.mark.asyncio
async def test_overpass_skips_missing_name_or_website_and_deduplicates() -> None:
    payload = {
        "elements": [
            {"type": "node", "id": 1, "lat": 1.0, "lon": 2.0, "tags": {"name": "No site"}},
            {"type": "node", "id": 2, "lat": 1.0, "lon": 2.0, "tags": {"website": "https://unnamed.example.com"}},
            {"type": "node", "id": 3, "lat": 1.0, "lon": 2.0, "tags": {"name": "A", "website": "https://same.example.com"}},
            {"type": "way", "id": 4, "center": {"lat": 1.0, "lon": 2.0}, "tags": {"name": "B", "website": "https://same.example.com/"}},
            {"type": "relation", "id": 5, "center": {"lat": 1.0, "lon": 2.0}, "tags": {"name": "C", "website": "https://unique.example.com"}},
            {"type": "relation", "id": 5, "center": {"lat": 1.0, "lon": 2.0}, "tags": {"name": "C duplicate", "website": "https://another.example.com"}},
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _json_response(payload))
    ) as client:
        seeds = await OverpassDiscoverySource(client=client).discover(MARKET, "other", limit=20)

    assert [seed.business_name for seed in seeds] == ["A", "C"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(200, content=b"not-json", headers={"Content-Type": "application/json"}), DiscoveryPayloadError),
        (_json_response({"elements": "wrong"}), DiscoveryPayloadError),
        (_json_response({"elements": []}, 400), DiscoveryPayloadError),
        (_json_response({"elements": []}, 429), DiscoveryRateLimitError),
        (_json_response({"elements": []}, 503), DiscoveryProviderError),
    ],
)
async def test_overpass_rejects_malformed_and_error_responses_safely(
    response: httpx.Response, error: type[Exception]
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    ) as client:
        with pytest.raises(error):
            await OverpassDiscoverySource(client=client).discover(MARKET, "other", limit=1)


@pytest.mark.asyncio
async def test_overpass_rejects_response_over_byte_budget() -> None:
    response = httpx.Response(
        200,
        content=b'{"elements":[]}',
        headers={"Content-Type": "application/json", "Content-Length": "15"},
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    ) as client:
        with pytest.raises(DiscoveryPayloadError):
            await OverpassDiscoverySource(client=client, max_response_bytes=14).discover(
                MARKET, "other", limit=1
            )


@pytest.mark.asyncio
async def test_overpass_rejects_payload_element_cap() -> None:
    payload = {"elements": [{}] * 201}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _json_response(payload))
    ) as client:
        with pytest.raises(DiscoveryPayloadError):
            await OverpassDiscoverySource(client=client).discover(MARKET, "other", limit=200)


@pytest.mark.parametrize("radius_km", [80.0001, 100.0])
@pytest.mark.asyncio
async def test_overpass_rejects_market_radius_over_80_km(radius_km: float) -> None:
    market = MARKET.model_copy(update={"radius_km": radius_km})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _json_response({"elements": []}))
    ) as client:
        with pytest.raises(DiscoveryConfigurationError, match="80 km"):
            await OverpassDiscoverySource(client=client).discover(market, "other", limit=1)


@pytest.mark.parametrize("timeout", [0, 30.0001, float("inf")])
def test_overpass_timeout_is_positive_finite_and_at_most_30_seconds(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        OverpassDiscoverySource(timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_overpass_readiness_repr_and_client_lifecycle() -> None:
    source = OverpassDiscoverySource()
    assert source.name == "openstreetmap-overpass"
    assert source.readiness() == "ready"
    assert repr(source) == "OverpassDiscoverySource(ready=True)"
    client = source._client
    async with source as entered:
        assert entered is source
    assert client.is_closed
    await source.aclose()

    external = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _json_response({"elements": []}))
    )
    provider = OverpassDiscoveryProvider(external)
    await provider.aclose()
    assert not external.is_closed
    await external.aclose()
