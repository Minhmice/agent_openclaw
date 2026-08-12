from __future__ import annotations

from collections.abc import Iterable

import httpx
import pytest

from openclaw_web.crawl.service import CrawlLimits, WebsiteCrawler

PUBLIC_IP = "93.184.216.34"


def public_resolver(_host: str) -> Iterable[str]:
    return (PUBLIC_IP,)


def html(body: str, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(
        status,
        text=body,
        headers={"Content-Type": "text/html; charset=utf-8", **headers},
    )


def client_for(routes: dict[str, httpx.Response | Exception], requests: list[str]) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        response = routes.get(url)
        if isinstance(response, Exception):
            raise response
        if response is None:
            return httpx.Response(404, text="missing")
        response.request = request
        return response

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


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
    crawler = WebsiteCrawler(client=client, resolver=public_resolver, limits=CrawlLimits(max_pages=3))

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
                '<a href="/large">Large</a><a href="/json">JSON</a>'
                '<a href="/level-one">One</a>'
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
    times = iter((0.0, 0.0, 0.0, 0.0, 0.0))
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

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
        monotonic=lambda: next(times),
        sleeper=sleep,
    ).crawl("https://example.com/")
    assert any(failure.reason == "robots disallowed" for failure in result.failures)
    assert sleeps == [2.0]
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
async def test_crawler_closes_only_owned_client_and_can_be_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = False

    async def fake_close(_self: httpx.AsyncClient) -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr(httpx.AsyncClient, "aclose", fake_close)
    crawler = WebsiteCrawler(resolver=public_resolver, transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    await crawler.aclose()
    assert closed
