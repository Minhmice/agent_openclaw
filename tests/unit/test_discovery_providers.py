from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from openclaw_web.discovery import (
    APPROVED_COHORTS,
    DiscoveryConfigurationError,
    DiscoveryPayloadError,
    DiscoveryProviderError,
    DiscoveryRateLimitError,
    GooglePlacesDiscoveryProvider,
    GooglePlacesDiscoverySource,
    SerperDiscoveryProvider,
    SerperDiscoverySource,
)
from openclaw_web.discovery.base import AsyncRateLimiter
from openclaw_web.settings import MarketCenter, MarketConfig

NOW = datetime(2026, 8, 12, tzinfo=UTC)
MARKET = MarketConfig(
    market_id="hanoi",
    center=MarketCenter(name="Hà Nội", latitude=21.0278, longitude=105.8342),
    radius_km=80.0,
    industries=["manufacturing"],
    timezone="Asia/Bangkok",
)


def _clock() -> datetime:
    return NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", [SerperDiscoverySource, GooglePlacesDiscoverySource])
async def test_exact_source_constructor_owns_optional_client_lifecycle(
    source_type: type[SerperDiscoverySource | GooglePlacesDiscoverySource],
) -> None:
    source = source_type(api_key="secret")

    assert source.readiness() == "ready"
    owned_client = source._client
    async with source as entered:
        assert entered is source
        assert not owned_client.is_closed
    assert owned_client.is_closed
    await source.aclose()


@pytest.mark.parametrize("key", ["", "  "])
def test_providers_reject_blank_keys(key: str) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    with pytest.raises(DiscoveryConfigurationError, match="API credential is not configured"):
        SerperDiscoveryProvider(client, key)
    with pytest.raises(DiscoveryConfigurationError, match="API credential is not configured"):
        GooglePlacesDiscoveryProvider(client, key)


@pytest.mark.asyncio
async def test_serper_uses_vietnamese_cohort_queries_auth_and_canonical_results() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"organic": [{"title": "Nhà máy Việt", "link": "HTTPS://WWW.Example.COM"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SerperDiscoveryProvider(client, SecretStr("top-secret"), clock=_clock)
        seeds = await provider.discover(MARKET, "manufacturer", limit=1)

    request = requests[0]
    assert request.url == "https://google.serper.dev/search"
    assert request.headers["X-API-KEY"] == "top-secret"
    assert request.headers["Content-Type"] == "application/json"
    assert "Hà Nội" in request.content.decode()
    assert "nhà sản xuất" in request.content.decode()
    assert str(seeds[0].url) == "https://www.example.com/"
    assert "top-secret" not in repr(provider)
    assert "top-secret" not in str(seeds[0].source_url)


@pytest.mark.asyncio
async def test_serper_accepts_realistic_response_envelope() -> None:
    fixture_path = (
        Path(__file__).parents[1] / "fixtures" / "discovery" / "serper-search-redacted.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=fixture))
    ) as client:
        seeds = await SerperDiscoverySource("secret", client=client).discover(
            MARKET, "other", limit=1
        )
    assert [seed.business_name for seed in seeds] == ["Example Company"]


@pytest.mark.asyncio
async def test_serper_validates_only_the_bounded_organic_prefix_it_consumes() -> None:
    fixture = {
        "organic": [
            {"title": "Selected", "link": "https://selected.example.com"},
            *[{"title": "not consumed because limit is satisfied"}] * 100,
        ],
        "knowledgeGraph": {"title": "ordinary extra envelope field"},
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=fixture))
    ) as client:
        seeds = await SerperDiscoverySource("secret", client=client).discover(
            MARKET, "other", limit=1
        )

    assert [seed.business_name for seed in seeds] == ["Selected"]


@pytest.mark.asyncio
async def test_serper_never_consumes_more_organic_entries_than_requested_page_size() -> None:
    fixture = {
        "organic": [
            {"title": "Unsafe", "link": "http://127.0.0.1"},
            {"title": "outside the requested page prefix"},
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=fixture))
    ) as client:
        seeds = await SerperDiscoverySource(
            "secret", client=client, page_size=1, max_pages=1
        ).discover(MARKET, "other", limit=2)

    assert seeds == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("cohort", APPROVED_COHORTS)
async def test_serper_has_query_template_for_every_approved_cohort(cohort: str) -> None:
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        return httpx.Response(200, json={"organic": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await SerperDiscoveryProvider(client, "secret", clock=_clock).discover(
            MARKET, cohort, limit=1
        )

    assert "Hà Nội" in bodies[0]


@pytest.mark.asyncio
async def test_serper_paginates_stably_deduplicates_and_stops_at_limit() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(
            httpx.Request("GET", "https://x", content=request.content).read()
            and __import__("json").loads(request.content)["page"]
        )
        pages.append(page)
        return httpx.Response(
            200,
            json={
                "organic": [
                    {"title": f"Business {page}", "link": f"https://{page}.example.com"},
                    {"title": "Duplicate", "link": "https://1.example.com"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        seeds = await SerperDiscoveryProvider(
            client, "secret", clock=_clock, page_size=2, max_pages=4
        ).discover(MARKET, "other", limit=3)

    assert pages == [1, 2, 3]
    assert len(seeds) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, DiscoveryConfigurationError),
        (429, DiscoveryRateLimitError),
        (500, DiscoveryProviderError),
    ],
)
async def test_provider_http_errors_are_typed_and_sanitized(
    status: int, error: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"Retry-After": "3"}, text="secret-body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SerperDiscoveryProvider(client, "top-secret", clock=_clock)
        with pytest.raises(error) as raised:
            await provider.discover(MARKET, "other", limit=1)

    message = str(raised.value)
    assert "top-secret" not in message
    assert "secret-body" not in message
    if isinstance(raised.value, DiscoveryRateLimitError):
        assert raised.value.retry_after == 3.0


@pytest.mark.asyncio
async def test_retry_after_http_date_uses_injected_utc_clock_and_clamps() -> None:
    retry_at = format_datetime(NOW + timedelta(seconds=90), usegmt=True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": retry_at})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DiscoveryRateLimitError) as raised:
            await SerperDiscoverySource("secret", client=client, clock=_clock).discover(
                MARKET, "other", limit=1
            )
    assert raised.value.retry_after == 90.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("-10", 0.0),
        ("7200", 3600.0),
        (format_datetime(NOW - timedelta(seconds=1), usegmt=True), 0.0),
        (format_datetime(NOW + timedelta(hours=2), usegmt=True), 3600.0),
    ],
)
async def test_retry_after_delta_and_http_date_are_clamped(header: str, expected: float) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": header})
        )
    ) as client:
        with pytest.raises(DiscoveryRateLimitError) as raised:
            await SerperDiscoverySource("secret", client=client, clock=_clock).discover(
                MARKET, "other", limit=1
            )
    assert raised.value.retry_after == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({"Content-Type": "text/html"}, b"{}"),
        ({"Content-Type": "application/json", "Content-Length": "999999"}, b"{}"),
        ({"Content-Type": "application/json"}, b"{"),
        ({"Content-Type": "application/json"}, b"[" * 80 + b"]" * 80),
    ],
)
async def test_streamed_json_rejects_wrong_media_oversize_malformed_and_depth(
    headers: dict[str, str], body: bytes
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers=headers, content=body)
        )
    ) as client:
        with pytest.raises(DiscoveryPayloadError, match="invalid provider payload"):
            await SerperDiscoverySource("secret", client=client, max_response_bytes=32).discover(
                MARKET, "other", limit=1
            )


@pytest.mark.asyncio
async def test_streamed_json_rejects_chunked_body_over_cap() -> None:
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"organic":['
            yield b" " * 64

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/problem+json"}, stream=Chunks()
            )
        )
    ) as client:
        with pytest.raises(DiscoveryPayloadError, match="invalid provider payload"):
            await SerperDiscoverySource("secret", client=client, max_response_bytes=32).discover(
                MARKET, "other", limit=1
            )


@pytest.mark.asyncio
async def test_streamed_json_accepts_chunked_vendor_json_and_closes_stream() -> None:
    class Chunks(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'{"organic":['
            yield b'{"title":"X","link":"https://x.example.com"}]}'

        async def aclose(self) -> None:
            self.closed = True

    stream = Chunks()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/vnd.serper+json"}, stream=stream
            )
        )
    ) as client:
        seeds = await SerperDiscoverySource("secret", client=client).discover(
            MARKET, "other", limit=1
        )

    assert [seed.business_name for seed in seeds] == ["X"]
    assert stream.closed


@pytest.mark.asyncio
async def test_streamed_json_closes_stream_when_consumer_is_cancelled() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Chunks(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            entered.set()
            yield b'{"organic":['
            await release.wait()

        async def aclose(self) -> None:
            self.closed = True

    stream = Chunks()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/json"}, stream=stream
            )
        )
    ) as client:
        task = asyncio.create_task(
            SerperDiscoverySource("secret", client=client).discover(MARKET, "other", limit=1)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert stream.closed


@pytest.mark.asyncio
async def test_timeout_and_malformed_payload_are_sanitized() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("credential=top-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(DiscoveryProviderError, match="request failed") as raised:
            await SerperDiscoveryProvider(client, "top-secret").discover(MARKET, "other", limit=1)
        assert "top-secret" not in str(raised.value)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"organic": "x"}))
    ) as client:
        with pytest.raises(DiscoveryPayloadError, match="invalid provider payload"):
            await SerperDiscoveryProvider(client, "secret").discover(MARKET, "other", limit=1)


@pytest.mark.asyncio
async def test_async_rate_limiter_uses_injected_monotonic_and_sleeper() -> None:
    times = iter([0.0, 0.25, 1.25])
    slept: list[float] = []

    async def sleep(delay: float) -> None:
        slept.append(delay)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"organic": []}))
    ) as client:
        provider = SerperDiscoveryProvider(
            client,
            "secret",
            monotonic=lambda: next(times),
            sleeper=sleep,
            min_interval_seconds=1.0,
        )
        await provider.discover(MARKET, "other", limit=2)
        await provider.discover(MARKET, "other", limit=2)

    assert slept == [0.75]


@pytest.mark.asyncio
async def test_async_rate_limiter_serializes_concurrent_callers() -> None:
    now = 0.0
    slept: list[float] = []

    async def sleep(delay: float) -> None:
        nonlocal now
        slept.append(delay)
        await asyncio.sleep(0)
        now += delay

    limiter = AsyncRateLimiter(1.0, monotonic=lambda: now, sleeper=sleep)
    await asyncio.gather(*(limiter.wait() for _ in range(3)))

    assert slept == [1.0, 1.0]


@pytest.mark.asyncio
async def test_cancelled_rate_limiter_waiter_does_not_consume_a_slot() -> None:
    now = 0.0
    sleeps: list[float] = []
    first_sleep_entered = asyncio.Event()
    release = asyncio.Event()

    async def sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        if len(sleeps) == 1:
            first_sleep_entered.set()
            await release.wait()
        now += delay

    limiter = AsyncRateLimiter(1.0, monotonic=lambda: now, sleeper=sleep)
    await limiter.wait()
    cancelled = asyncio.create_task(limiter.wait())
    await first_sleep_entered.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    await limiter.wait()
    assert sleeps == [1.0, 1.0]


@pytest.mark.asyncio
async def test_injected_provider_client_is_never_closed_by_source() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"organic": []}))
    )
    source = SerperDiscoverySource("secret", client=client)
    await source.aclose()
    assert not client.is_closed
    await client.aclose()


@pytest.mark.parametrize(
    ("source_type", "kwargs"),
    [
        (SerperDiscoverySource, {"max_response_bytes": 0}),
        (SerperDiscoverySource, {"max_response_bytes": 5_000_001}),
        (GooglePlacesDiscoverySource, {"max_response_bytes": 0}),
        (GooglePlacesDiscoverySource, {"max_response_bytes": 5_000_001}),
        (GooglePlacesDiscoverySource, {"max_requests": 0}),
        (GooglePlacesDiscoverySource, {"max_requests": 401}),
    ],
)
def test_provider_resource_bounds_are_validated(
    source_type: type[SerperDiscoverySource | GooglePlacesDiscoverySource],
    kwargs: dict[str, int],
) -> None:
    with pytest.raises(ValueError):
        source_type("secret", **kwargs)


@pytest.mark.asyncio
async def test_places_preserves_coordinates_and_https_evidence() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "places": [
                    {
                        "id": "place-1",
                        "displayName": {"text": "Nhà máy Hà Nội"},
                        "formattedAddress": "Hà Nội, Việt Nam",
                        "location": {"latitude": 21.03, "longitude": 105.83},
                        "websiteUri": "https://example.vn",
                        "googleMapsUri": "https://maps.google.com/?cid=123",
                    },
                    {
                        "id": "place-no-site",
                        "displayName": {"text": "No site"},
                        "location": {"latitude": 21.0, "longitude": 105.8},
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        seeds = await GooglePlacesDiscoveryProvider(client, "top-secret", clock=_clock).discover(
            MARKET, "manufacturer", limit=2
        )

    assert len(seeds) == 1
    seed = seeds[0]
    assert (seed.latitude, seed.longitude) == (21.03, 105.83)
    assert seed.address == "Hà Nội, Việt Nam"
    assert seed.external_id == "place-1"
    assert str(seed.source_url).startswith("https://maps.google.com/")
    request = requests[0]
    assert request.url == "https://places.googleapis.com/v1/places:searchText"
    assert request.headers["X-Goog-Api-Key"] == "top-secret"
    assert "locationBias" in request.content.decode()
    assert "locationRestriction" not in request.content.decode()
    assert "Hà Nội" in request.content.decode()


@pytest.mark.asyncio
async def test_places_stops_on_repeated_page_token() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"places": [], "nextPageToken": "same"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await GooglePlacesDiscoveryProvider(client, "secret", max_pages=5).discover(
            MARKET, "other", limit=10
        )

    assert calls == 14


def _haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(value))


def _destination(
    center: tuple[float, float], distance_km: float, bearing: float
) -> tuple[float, float]:
    lat, lon = map(math.radians, center)
    angular = distance_km / 6371.0088
    direction = math.radians(bearing)
    target_lat = math.asin(
        math.sin(lat) * math.cos(angular) + math.cos(lat) * math.sin(angular) * math.cos(direction)
    )
    target_lon = lon + math.atan2(
        math.sin(direction) * math.sin(angular) * math.cos(lat),
        math.cos(angular) - math.sin(lat) * math.sin(target_lat),
    )
    return math.degrees(target_lat), ((math.degrees(target_lon) + 180) % 360) - 180


@pytest.mark.asyncio
async def test_places_partitions_full_80km_market_with_exact_bounded_circles() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"places": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await GooglePlacesDiscoverySource(
            api_key="secret", client=client, max_pages=1, max_requests=7
        ).discover(MARKET, "other", limit=10)

    assert len(bodies) == 7
    circles = [body["locationBias"]["circle"] for body in bodies]  # type: ignore[index]
    assert all(circle["radius"] == 50_000.0 for circle in circles)  # type: ignore[index]
    assert circles[0]["center"] == {"latitude": 21.0278, "longitude": 105.8342}  # type: ignore[index]
    assert len({json.dumps(circle, sort_keys=True) for circle in circles}) == 7

    expected_centers = [
        market_center := (MARKET.center.latitude, MARKET.center.longitude),
        *[_destination(market_center, 40, bearing) for bearing in range(0, 360, 60)],
    ]
    for circle, expected in zip(circles, expected_centers, strict=True):
        assert (
            circle["center"]["latitude"],  # type: ignore[index]
            circle["center"]["longitude"],  # type: ignore[index]
        ) == pytest.approx(expected)

    query_circles = [
        (
            (circle["center"]["latitude"], circle["center"]["longitude"]),  # type: ignore[index]
            circle["radius"] / 1000,  # type: ignore[index,operator]
        )
        for circle in circles
    ]
    for bearing in range(0, 360, 5):
        boundary = _destination(market_center, MARKET.radius_km, bearing)
        assert any(_haversine_km(boundary, center) <= radius for center, radius in query_circles)


@pytest.mark.asyncio
async def test_places_rejects_partition_that_exceeds_request_budget_before_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"places": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = GooglePlacesDiscoverySource(
            api_key="secret", client=client, max_pages=2, max_requests=6
        )
        with pytest.raises(DiscoveryConfigurationError, match="request budget"):
            await source.discover(MARKET, "other", limit=10)

    assert calls == 0


@pytest.mark.asyncio
async def test_places_queries_every_partition_before_global_limit_selection() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        center = body["locationBias"]["circle"]["center"]
        index = len(bodies) - 1
        return httpx.Response(
            200,
            json={
                "places": [
                    {
                        "id": f"place-{index}",
                        "displayName": {"text": f"Business {index}"},
                        "location": center,
                        "websiteUri": f"https://{index}.example.com",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        seeds = await GooglePlacesDiscoverySource(
            "secret", client=client, max_pages=3, max_requests=7
        ).discover(MARKET, "other", limit=1)
    assert len(bodies) == 7
    assert [seed.external_id for seed in seeds] == ["place-0"]


@pytest.mark.asyncio
async def test_places_global_selection_preserves_fair_center_order_not_url_sort() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        index = calls
        calls += 1
        body = json.loads(request.content)
        center = body["locationBias"]["circle"]["center"]
        return httpx.Response(
            200,
            json={
                "places": [
                    {
                        "id": f"place-{index}",
                        "displayName": {"text": f"Business {index}"},
                        "location": center,
                        "websiteUri": f"https://{9 - index}.example.com",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        seeds = await GooglePlacesDiscoverySource(
            "secret", client=client, max_pages=1, max_requests=7
        ).discover(MARKET, "other", limit=2)

    assert calls == 7
    assert [seed.external_id for seed in seeds] == ["place-0", "place-1"]


@pytest.mark.asyncio
async def test_places_scopes_page_tokens_to_centers_and_deduplicates_results() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "pageToken" not in body:
            return httpx.Response(
                200,
                json={
                    "places": [
                        {
                            "id": "same",
                            "displayName": {"text": "Same"},
                            "location": {"latitude": 21.03, "longitude": 105.83},
                            "websiteUri": "https://same.example.com",
                        }
                    ],
                    "nextPageToken": "shared-token",
                },
            )
        return httpx.Response(200, json={"places": [], "nextPageToken": "shared-token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        seeds = await GooglePlacesDiscoverySource(
            api_key="secret", client=client, max_pages=2, max_requests=14
        ).discover(MARKET, "other", limit=200)

    assert len(requests) == 14
    assert sum(body.get("pageToken") == "shared-token" for body in requests) == 7
    assert len(seeds) == 1
    assert seeds[0].metadata["search_center"] == 0


@pytest.mark.parametrize("limit", [True, 0, -1, 201])
@pytest.mark.asyncio
async def test_provider_limit_is_strictly_bounded(limit: object) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"organic": []}))
    ) as client:
        with pytest.raises((TypeError, ValueError)):
            await SerperDiscoveryProvider(client, "secret").discover(  # type: ignore[arg-type]
                MARKET, "other", limit=limit
            )
