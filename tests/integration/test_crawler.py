from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable

import httpx
import pytest

from openclaw_web.crawl.service import CrawlLimits, WebsiteCrawler

PUBLIC_IP = "93.184.216.34"


class PeerStream:
    def __init__(self, host: str) -> None:
        self._host = host

    def get_extra_info(self, name: str) -> object:
        if name == "server_addr":
            return (self._host, 443)
        return None


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def public_resolver(_host: str) -> Iterable[str]:
    return (PUBLIC_IP,)


def html(body: str, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(
        status,
        text=body,
        headers={"Content-Type": "text/html; charset=utf-8", **headers},
    )


def client_for(
    routes: dict[str, httpx.Response | Exception],
    requests: list[str],
    *,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        response = routes.get(url)
        if isinstance(response, Exception):
            raise response
        if response is None:
            response = httpx.Response(404, text="missing")
        response.request = request
        response.extensions.setdefault("network_stream", PeerStream(PUBLIC_IP))
        return response

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=follow_redirects
    )


@pytest.mark.asyncio
async def test_injected_client_ambient_credentials_are_never_used_on_any_hop() -> None:
    captured: dict[str, dict[str, str]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured[str(request.url)] = dict(request.headers)
        routes = {
            "https://www.example.com/robots.txt": httpx.Response(404),
            "https://www.example.com/": httpx.Response(
                302, headers={"Location": "https://shop.example.com/final"}
            ),
            "https://shop.example.com/robots.txt": httpx.Response(404),
            "https://shop.example.com/final": html("Final"),
        }
        response = routes[str(request.url)]
        response.request = request
        response.extensions["network_stream"] = PeerStream(PUBLIC_IP)
        return response

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer ambient", "X-Ambient-Key": "secret"},
        cookies={"session": "ambient"},
        auth=httpx.BasicAuth("ambient-user", "ambient-password"),
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://www.example.com/"
    )

    assert [page.url for page in result.pages] == ["https://shop.example.com/final"]
    assert set(captured) == {
        "https://www.example.com/robots.txt",
        "https://www.example.com/",
        "https://shop.example.com/robots.txt",
        "https://shop.example.com/final",
    }
    # Crawler requests deliberately carry only its safe identity/content headers; HTTPX
    # derives Host from each validated URL for correct routing and TLS SNI.
    for headers in captured.values():
        assert set(headers) == {"host", "accept", "user-agent"}
        assert headers["accept"] == "text/html,application/xhtml+xml"
        assert headers["user-agent"] == "OpenClawWebAudit/1.0"
    await client.aclose()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_timeout_seconds", 0),
        ("request_timeout_seconds", -1),
        ("request_timeout_seconds", math.nan),
        ("request_timeout_seconds", math.inf),
        ("request_timeout_seconds", True),
        ("request_timeout_seconds", 301),
        ("page_timeout_seconds", 0),
        ("page_timeout_seconds", -1),
        ("page_timeout_seconds", math.nan),
        ("page_timeout_seconds", math.inf),
        ("page_timeout_seconds", True),
        ("page_timeout_seconds", 301),
    ],
)
def test_crawl_timeout_limits_must_be_finite_positive_and_bounded(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError, match="timeout"):
        CrawlLimits(**{field: value})


def test_page_timeout_must_cover_at_least_one_request() -> None:
    with pytest.raises(ValueError, match="page timeout"):
        CrawlLimits(request_timeout_seconds=2, page_timeout_seconds=1)


@pytest.mark.asyncio
async def test_request_timeout_overrides_injected_client_with_no_timeout() -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        await asyncio.sleep(1)
        return httpx.Response(404, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=None)
    result = await asyncio.wait_for(
        WebsiteCrawler(
            client=client,
            resolver=public_resolver,
            limits=CrawlLimits(request_timeout_seconds=0.01, page_timeout_seconds=0.1),
        ).crawl("https://example.com/"),
        timeout=0.5,
    )

    assert requested == ["https://example.com/robots.txt"]
    assert result.pages == ()
    assert result.failures[0].reason == "robots policy unreachable"
    await client.aclose()


@pytest.mark.asyncio
async def test_page_timeout_bounds_the_complete_redirect_operation() -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url == "https://example.com/robots.txt":
            response = httpx.Response(404)
        else:
            await asyncio.sleep(0.2)
            if url == "https://example.com/":
                response = httpx.Response(302, headers={"Location": "/final"})
            else:
                response = html("Final")
        response.request = request
        response.extensions["network_stream"] = PeerStream(PUBLIC_IP)
        return response

    async def no_wait(_delay: float) -> None:
        return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=None)
    result = await WebsiteCrawler(
        client=client,
        resolver=public_resolver,
        limits=CrawlLimits(request_timeout_seconds=0.25, page_timeout_seconds=0.3),
        sleeper=no_wait,
    ).crawl("https://example.com/")

    assert requested == [
        "https://example.com/robots.txt",
        "https://example.com/",
        "https://example.com/final",
    ]
    assert result.pages == ()
    assert result.failures[0].reason == "page fetch failed"
    await client.aclose()


@pytest.mark.asyncio
async def test_exact_frontier_capacity_fully_processed_is_not_exhausted() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": html('<a href="/contact">Contact</a>'),
            "https://example.com/contact": html("Contact"),
        },
        requested,
    )

    result = await WebsiteCrawler(
        client=client,
        resolver=public_resolver,
        limits=CrawlLimits(max_pages=2, max_frontier_urls=2),
    ).crawl("https://example.com/")

    assert [page.url for page in result.pages] == [
        "https://example.com/",
        "https://example.com/contact",
    ]
    assert not result.budget_exhausted
    await client.aclose()


@pytest.mark.asyncio
async def test_refused_frontier_url_marks_budget_exhausted() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": html(
                '<a href="/contact">Contact</a><a href="/about">About</a>'
            ),
            "https://example.com/contact": html("Contact"),
        },
        requested,
    )

    result = await WebsiteCrawler(
        client=client,
        resolver=public_resolver,
        limits=CrawlLimits(max_pages=2, max_frontier_urls=2),
    ).crawl("https://example.com/")

    assert [page.url for page in result.pages] == [
        "https://example.com/",
        "https://example.com/contact",
    ]
    assert "https://example.com/about" not in requested
    assert result.budget_exhausted
    await client.aclose()


@pytest.mark.asyncio
async def test_crawler_rejects_response_from_peer_outside_validated_dns_answers() -> None:
    requested: list[str] = []
    mismatched_page = html("must not be accepted")
    mismatched_page.extensions["network_stream"] = PeerStream("1.1.1.1")
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": mismatched_page,
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://example.com/"
    )

    assert result.pages == ()
    assert len(result.failures) == 1
    assert result.failures[0].reason == "page fetch failed"
    await client.aclose()


@pytest.mark.asyncio
async def test_crawler_fails_closed_when_connected_peer_identity_is_unavailable() -> None:
    requested: list[str] = []
    unidentified_page = html("must not be accepted")
    unidentified_page.extensions["network_stream"] = object()
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": unidentified_page,
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://example.com/"
    )

    assert result.pages == ()
    assert len(result.failures) == 1
    assert result.failures[0].reason == "page fetch failed"
    await client.aclose()


@pytest.mark.asyncio
async def test_crawler_rejects_robots_response_from_mismatched_peer() -> None:
    requested: list[str] = []
    mismatched_robots = httpx.Response(404)
    mismatched_robots.extensions["network_stream"] = PeerStream("1.1.1.1")
    client = client_for(
        {
            "https://example.com/robots.txt": mismatched_robots,
            "https://example.com/": html("must not be fetched"),
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://example.com/"
    )

    assert result.pages == ()
    assert result.failures[0].reason == "robots policy unreachable"
    assert "https://example.com/" not in requested
    await client.aclose()


@pytest.mark.asyncio
async def test_crawler_rejects_redirect_response_from_mismatched_peer() -> None:
    requested: list[str] = []
    mismatched_redirect = httpx.Response(302, headers={"Location": "/destination"})
    mismatched_redirect.extensions["network_stream"] = PeerStream("1.1.1.1")
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": mismatched_redirect,
            "https://example.com/destination": html("must not be fetched"),
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://example.com/"
    )

    assert result.pages == ()
    assert result.failures[0].reason == "page fetch failed"
    assert "https://example.com/destination" not in requested
    await client.aclose()


@pytest.mark.asyncio
async def test_crawler_stops_at_page_budget_and_uses_deterministic_priority() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(
                200, text="User-agent: *\nAllow: /\n", headers={"Content-Type": "text/plain"}
            ),
            "https://example.com/": html(
                '<a href="/deep/irrelevant">Deep</a><a href="/about">About</a>'
                '<a href="/contact">Contact</a><a href="/services">Services</a>'
            ),
            "https://example.com/contact": html("<h1>Contact</h1>"),
            "https://example.com/services": html("<h1>Services</h1>"),
            "https://example.com/about": html("<h1>About</h1>"),
        },
        requested,
    )
    crawler = WebsiteCrawler(
        client=client, resolver=public_resolver, limits=CrawlLimits(max_pages=3)
    )

    result = await crawler.crawl("https://example.com/")

    assert [page.url for page in result.pages] == [
        "https://example.com/",
        "https://example.com/contact",
        "https://example.com/services",
    ]
    assert result.budget_exhausted
    assert not client.is_closed
    await client.aclose()


@pytest.mark.asyncio
async def test_depth_body_and_content_type_limits_are_enforced_once_per_page() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": html(
                '<a href="/large">Large</a><a href="/json">JSON</a><a href="/level-one">One</a>'
            ),
            "https://example.com/large": html("x" * 200),
            "https://example.com/json": httpx.Response(
                200, json={"secret": "value"}, headers={"Content-Type": "application/json"}
            ),
            "https://example.com/level-one": html('<a href="/too-deep">Two</a>'),
            "https://example.com/too-deep": html("never fetched"),
        },
        requested,
    )

    result = await WebsiteCrawler(
        client=client,
        resolver=public_resolver,
        limits=CrawlLimits(max_pages=5, max_depth=1, max_body_bytes=128),
    ).crawl("https://example.com/")

    assert {failure.url for failure in result.failures} == {
        "https://example.com/json",
        "https://example.com/large",
    }
    assert len(result.failures) == 2
    assert "https://example.com/too-deep" not in requested
    assert all("value" not in failure.reason for failure in result.failures)
    await client.aclose()


@pytest.mark.asyncio
async def test_redirect_loop_and_unsafe_redirect_are_bounded_and_sanitized() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": html('<a href="/loop">Loop</a><a href="/unsafe">Unsafe</a>'),
            "https://example.com/loop": httpx.Response(302, headers={"Location": "/loop"}),
            "https://example.com/unsafe": httpx.Response(
                302, headers={"Location": "http://127.0.0.1/private?token=secret"}
            ),
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://example.com/"
    )

    assert len(result.failures) == 2
    assert {failure.reason for failure in result.failures} == {
        "redirect loop",
        "unsafe crawl target",
    }
    assert all("secret" not in failure.reason for failure in result.failures)
    await client.aclose()


@pytest.mark.asyncio
async def test_robots_unreachable_denies_and_loaded_policy_honors_delay() -> None:
    unavailable_requests: list[str] = []
    unavailable_client = client_for(
        {
            "https://example.com/robots.txt": httpx.ConnectError("token=secret"),
        },
        unavailable_requests,
    )
    denied = await WebsiteCrawler(client=unavailable_client, resolver=public_resolver).crawl(
        "https://example.com/"
    )
    assert denied.pages == ()
    assert denied.failures[0].reason == "robots policy unreachable"
    await unavailable_client.aclose()

    requested: list[str] = []
    clock = FakeClock()

    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(
                200,
                text="User-agent: *\nDisallow: /private\nCrawl-delay: 2\n",
                headers={"Content-Type": "text/plain"},
            ),
            "https://example.com/": html(
                '<a href="/private">Private</a><a href="/public">Public</a>'
            ),
            "https://example.com/public": html("Public"),
        },
        requested,
    )
    result = await WebsiteCrawler(
        client=client,
        resolver=public_resolver,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).crawl("https://example.com/")
    assert any(failure.reason == "robots disallowed" for failure in result.failures)
    assert clock.sleeps == [2.0, 2.0]
    await client.aclose()


@pytest.mark.asyncio
async def test_default_domain_rate_limit_applies_without_crawl_delay() -> None:
    requested: list[str] = []
    clock = FakeClock()
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": html("Home"),
        },
        requested,
    )

    result = await WebsiteCrawler(
        client=client,
        resolver=public_resolver,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).crawl("https://example.com/")

    assert [page.url for page in result.pages] == ["https://example.com/"]
    assert requested == ["https://example.com/robots.txt", "https://example.com/"]
    assert clock.sleeps == [1.0]
    await client.aclose()


@pytest.mark.asyncio
async def test_default_domain_rate_limit_is_shared_across_subdomains_schemes_and_ports() -> None:
    requested: list[str] = []
    clock = FakeClock()
    client = client_for(
        {
            "https://www.example.com/robots.txt": httpx.Response(404),
            "https://www.example.com/": html(
                '<a href="http://shop.example.com:8443/contact">Contact</a>'
            ),
            "http://shop.example.com:8443/robots.txt": httpx.Response(404),
            "http://shop.example.com:8443/contact": html("Contact"),
        },
        requested,
    )

    result = await WebsiteCrawler(
        client=client,
        resolver=public_resolver,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).crawl("https://www.example.com/")

    assert [page.url for page in result.pages] == [
        "https://www.example.com/",
        "http://shop.example.com:8443/contact",
    ]
    assert clock.sleeps == [1.0, 1.0, 1.0]
    await client.aclose()


@pytest.mark.asyncio
async def test_robots_crawl_delay_larger_than_default_domain_rate_limit_wins() -> None:
    requested: list[str] = []
    clock = FakeClock()
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(
                200,
                text="User-agent: *\nAllow: /\nCrawl-delay: 2\n",
                headers={"Content-Type": "text/plain"},
            ),
            "https://example.com/": html("Home"),
        },
        requested,
    )

    result = await WebsiteCrawler(
        client=client,
        resolver=public_resolver,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).crawl("https://example.com/")

    assert [page.url for page in result.pages] == ["https://example.com/"]
    assert clock.sleeps == [2.0]
    await client.aclose()


@pytest.mark.asyncio
async def test_same_registrable_domain_subdomain_allowed_external_and_duplicates_ignored() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://www.example.co.uk/robots.txt": httpx.Response(404),
            "https://www.example.co.uk/": html(
                '<a href="https://shop.example.co.uk/contact#one">Shop</a>'
                '<a href="https://shop.example.co.uk/contact#two">Duplicate</a>'
                '<a href="https://example.com/external">External</a>'
            ),
            "https://shop.example.co.uk/robots.txt": httpx.Response(404),
            "https://shop.example.co.uk/contact": html("Contact"),
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://www.example.co.uk/#fragment"
    )

    assert [page.url for page in result.pages] == [
        "https://www.example.co.uk/",
        "https://shop.example.co.uk/contact",
    ]
    assert requested.count("https://shop.example.co.uk/contact") == 1
    assert "https://example.com/external" not in requested
    await client.aclose()


@pytest.mark.asyncio
async def test_cross_site_redirect_is_rejected_before_destination_request() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": html('<a href="/leave">Leave</a>'),
            "https://example.com/leave": httpx.Response(
                302, headers={"Location": "https://other.net/private"}
            ),
            "https://other.net/private": html("must not be fetched"),
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://example.com/"
    )

    assert result.failures[-1].reason == "redirect leaves website"
    assert "https://other.net/private" not in requested
    await client.aclose()


@pytest.mark.asyncio
async def test_same_site_cross_origin_redirect_obeys_destination_robots() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://www.example.com/robots.txt": httpx.Response(404),
            "https://www.example.com/": httpx.Response(
                302, headers={"Location": "https://shop.example.com/private"}
            ),
            "https://shop.example.com/robots.txt": httpx.Response(
                200,
                text="User-agent: *\nDisallow: /private\n",
                headers={"Content-Type": "text/plain"},
            ),
            "https://shop.example.com/private": html("must not be fetched"),
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://www.example.com/"
    )

    assert result.pages == ()
    assert result.failures[0].reason == "robots disallowed"
    assert "https://shop.example.com/robots.txt" in requested
    assert "https://shop.example.com/private" not in requested
    await client.aclose()


@pytest.mark.asyncio
async def test_injected_redirect_following_cannot_bypass_destination_robots_peer_check() -> None:
    requested: list[str] = []
    mismatched_robots = httpx.Response(
        200,
        text="User-agent: *\nAllow: /\n",
        headers={"Content-Type": "text/plain"},
    )
    mismatched_robots.extensions["network_stream"] = PeerStream("1.1.1.1")
    client = client_for(
        {
            "https://www.example.com/robots.txt": httpx.Response(404),
            "https://www.example.com/": httpx.Response(
                302, headers={"Location": "https://shop.example.com/private"}
            ),
            "https://shop.example.com/robots.txt": mismatched_robots,
            "https://shop.example.com/private": html("must not be fetched"),
        },
        requested,
        follow_redirects=True,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://www.example.com/"
    )

    assert result.pages == ()
    assert result.failures[0].reason == "robots policy unreachable"
    assert "https://shop.example.com/robots.txt" in requested
    assert "https://shop.example.com/private" not in requested
    await client.aclose()


@pytest.mark.asyncio
async def test_same_site_cross_origin_redirect_fails_closed_when_robots_unreachable() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://www.example.com/robots.txt": httpx.Response(404),
            "https://www.example.com/": httpx.Response(
                302, headers={"Location": "https://shop.example.com/private"}
            ),
            "https://shop.example.com/robots.txt": httpx.ConnectError("token=secret"),
            "https://shop.example.com/private": html("must not be fetched"),
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://www.example.com/"
    )

    assert result.pages == ()
    assert result.failures[0].reason == "robots policy unreachable"
    assert "https://shop.example.com/private" not in requested
    assert all("secret" not in failure.reason for failure in result.failures)
    await client.aclose()


@pytest.mark.asyncio
async def test_same_site_cross_origin_redirect_uses_destination_crawl_delay() -> None:
    requested: list[str] = []
    clock = FakeClock()

    client = client_for(
        {
            "https://www.example.com/robots.txt": httpx.Response(404),
            "https://www.example.com/": httpx.Response(
                302, headers={"Location": "https://shop.example.com/first"}
            ),
            "https://shop.example.com/robots.txt": httpx.Response(
                200,
                text="User-agent: *\nAllow: /\nCrawl-delay: 2\n",
                headers={"Content-Type": "text/plain"},
            ),
            "https://shop.example.com/first": httpx.Response(302, headers={"Location": "/final"}),
            "https://shop.example.com/final": html("Final"),
        },
        requested,
    )

    result = await WebsiteCrawler(
        client=client,
        resolver=public_resolver,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).crawl("https://www.example.com/")

    assert [page.url for page in result.pages] == ["https://shop.example.com/final"]
    assert requested.count("https://shop.example.com/robots.txt") == 1
    assert clock.sleeps == [1.0, 1.0, 2.0, 2.0]
    await client.aclose()


@pytest.mark.asyncio
async def test_two_redirect_aliases_produce_one_canonical_page_record() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": html('<a href="/a">A</a><a href="/b">B</a>'),
            "https://example.com/a": httpx.Response(302, headers={"Location": "/canonical"}),
            "https://example.com/b": httpx.Response(302, headers={"Location": "/canonical"}),
            "https://example.com/canonical": html("Canonical"),
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://example.com/"
    )

    assert [page.url for page in result.pages] == [
        "https://example.com/",
        "https://example.com/canonical",
    ]
    await client.aclose()


@pytest.mark.asyncio
async def test_inner_failure_url_strips_query_and_fragment_without_losing_path() -> None:
    requested: list[str] = []
    client = client_for(
        {
            "https://example.com/robots.txt": httpx.Response(404),
            "https://example.com/": html(
                '<a href="/private/report?token=secret#details">Report</a>'
            ),
            "https://example.com/private/report?token=secret": html("failure", status=500),
        },
        requested,
    )

    result = await WebsiteCrawler(client=client, resolver=public_resolver).crawl(
        "https://example.com/"
    )

    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.url == "https://example.com/private/report"
    assert "?" not in failure.url
    assert "secret" not in failure.url
    assert "secret" not in failure.reason
    await client.aclose()


@pytest.mark.asyncio
async def test_crawler_closes_only_owned_client_and_can_be_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    async def fake_close(_self: httpx.AsyncClient) -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr(httpx.AsyncClient, "aclose", fake_close)
    crawler = WebsiteCrawler(
        resolver=public_resolver, transport=httpx.MockTransport(lambda _: httpx.Response(404))
    )
    await crawler.aclose()
    assert closed


@pytest.mark.asyncio
async def test_crawler_propagates_transport_cancellation() -> None:
    async def cancelled(_request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    client = httpx.AsyncClient(transport=httpx.MockTransport(cancelled))
    crawler = WebsiteCrawler(client=client, resolver=public_resolver)

    with pytest.raises(asyncio.CancelledError):
        await crawler.crawl("https://example.com/")

    await client.aclose()
