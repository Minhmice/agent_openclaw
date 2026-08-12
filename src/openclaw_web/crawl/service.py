"""Deterministic, bounded, robots-aware website crawler."""

from __future__ import annotations

import asyncio
import heapq
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Self, TypeAlias
from urllib.parse import urlsplit, urlunsplit

import httpx
import tldextract

from openclaw_web.crawl.extract import ExtractedPage, extract_page
from openclaw_web.crawl.robots import DEFAULT_MAX_BYTES, RobotsFetchOutcome, RobotsPolicy
from openclaw_web.crawl.safety import (
    Resolver,
    UnsafeTarget,
    normalize_url,
    resolve_and_validate,
    validate_redirect,
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_PRIORITY_TERMS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (0, ("contact", "lien-he", "quote", "bao-gia", "booking", "dat-lich")),
    (1, ("service", "dich-vu", "product", "san-pham", "catalog")),
    (2, ("about", "gioi-thieu", "company")),
)
_USER_AGENT = "OpenClawWebAudit/1.0"
_Extractor: TypeAlias = tldextract.TLDExtract


def _default_resolver(host: str) -> tuple[str, ...]:
    return tuple(str(item[4][0]) for item in socket.getaddrinfo(host, None))


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _priority(url: str) -> int:
    folded = urlsplit(url).path.casefold()
    for rank, terms in _PRIORITY_TERMS:
        if any(term in folded for term in terms):
            return rank
    return 3


def _registrable_domain(url: str, extractor: _Extractor) -> str:
    host = urlsplit(url).hostname or ""
    result = extractor(host)
    registered = result.top_domain_under_public_suffix
    return registered or host


@dataclass(frozen=True, slots=True)
class CrawlLimits:
    """Hard resource budgets for one crawl."""

    max_pages: int = 20
    max_depth: int = 2
    max_body_bytes: int = 2 * 1024 * 1024
    max_redirects: int = 5
    max_frontier_urls: int = 1_000
    max_robots_bytes: int = DEFAULT_MAX_BYTES

    def __post_init__(self) -> None:
        values = (
            self.max_pages,
            self.max_body_bytes,
            self.max_redirects,
            self.max_frontier_urls,
            self.max_robots_bytes,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
            raise ValueError("crawl limits must be positive integers")
        if isinstance(self.max_depth, bool) or not isinstance(self.max_depth, int) or self.max_depth < 0:
            raise ValueError("max_depth must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class CrawlFailure:
    """One sanitized terminal failure for one frontier URL."""

    url: str
    reason: str


@dataclass(frozen=True, slots=True)
class CrawlResult:
    """Immutable result of a bounded crawl."""

    start_url: str
    pages: tuple[ExtractedPage, ...]
    failures: tuple[CrawlFailure, ...]
    budget_exhausted: bool


class _DomainLimiter:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float],
        sleeper: Callable[[float], Awaitable[None]],
    ) -> None:
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_request: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, origin: str, delay: float | None) -> None:
        if delay is None or delay <= 0:
            return
        async with self._lock:
            now = self._monotonic()
            previous = self._last_request.get(origin)
            if previous is not None:
                remaining = delay - (now - previous)
                if remaining > 0:
                    await self._sleeper(remaining)
                    now += remaining
            self._last_request[origin] = now


class WebsiteCrawler:
    """Crawl one registrable website without executing JavaScript or submitting forms."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = _default_resolver,
        limits: CrawlLimits | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("transport cannot be supplied with an injected client")
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(transport=transport, follow_redirects=False)
        self._resolver = resolver
        self._limits = limits or CrawlLimits()
        self._extractor = tldextract.TLDExtract(suffix_list_urls=())
        self._limiter = _DomainLimiter(monotonic=monotonic, sleeper=sleeper)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owned_client and not self._client.is_closed:
            await self._client.aclose()

    async def _read_bounded(self, response: httpx.Response, maximum: int) -> bytes:
        length = response.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > maximum:
                    raise ValueError("response body exceeds byte limit")
            except ValueError as error:
                if str(error) == "response body exceeds byte limit":
                    raise
                raise ValueError("invalid content length") from None
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > maximum:
                raise ValueError("response body exceeds byte limit")
            content.extend(chunk)
        return bytes(content)

    async def _request(
        self,
        url: str,
        *,
        maximum: int,
        delay: float | None = None,
        allowed_site: str | None = None,
        allowed_origin: str | None = None,
    ) -> tuple[httpx.Response, bytes, str]:
        current = normalize_url(url)
        visited = {current}
        for redirect_count in range(self._limits.max_redirects + 1):
            host = urlsplit(current).hostname or ""
            resolve_and_validate(host, self._resolver)
            await self._limiter.wait(_origin(current), delay)
            async with self._client.stream(
                "GET", current, headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": _USER_AGENT}
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    if redirect_count >= self._limits.max_redirects:
                        raise ValueError("redirect limit exceeded")
                    try:
                        destination = validate_redirect(
                            current, response.headers.get("location", ""), self._resolver
                        )
                    except UnsafeTarget:
                        raise ValueError("unsafe crawl target") from None
                    if allowed_origin is not None and _origin(destination) != allowed_origin:
                        raise ValueError("redirect leaves origin")
                    if (
                        allowed_site is not None
                        and _registrable_domain(destination, self._extractor) != allowed_site
                    ):
                        raise ValueError("redirect leaves website")
                    if destination in visited:
                        raise ValueError("redirect loop")
                    visited.add(destination)
                    current = destination
                    continue
                body = await self._read_bounded(response, maximum)
                return response, body, current
        raise ValueError("redirect limit exceeded")

    async def _robots_policy(self, origin: str) -> RobotsPolicy:
        robots_url = f"{origin}/robots.txt"
        try:
            response, body, final_url = await self._request(
                robots_url,
                maximum=self._limits.max_robots_bytes,
                allowed_origin=origin,
            )
        except (httpx.HTTPError, ValueError, UnsafeTarget):
            return RobotsPolicy.unreachable("fetch failed", origin=origin)
        if _origin(final_url) != origin:
            return RobotsPolicy.unreachable("cross-origin redirect", origin=origin)
        if 200 <= response.status_code < 300:
            try:
                text = body.decode(response.encoding or "utf-8", errors="replace")
                return RobotsPolicy.parse(text, origin=origin, max_bytes=self._limits.max_robots_bytes)
            except (ValueError, UnicodeError):
                return RobotsPolicy.unreachable("invalid robots policy", origin=origin)
        if 400 <= response.status_code < 500:
            return RobotsPolicy.unavailable("HTTP unavailable", origin=origin, default_delay=0.001)
        return RobotsPolicy.unreachable("server failure", origin=origin)

    async def _fetch_page(
        self, url: str, policy: RobotsPolicy, *, allowed_site: str
    ) -> ExtractedPage:
        delay = policy.crawl_delay(_USER_AGENT)
        response, body, final_url = await self._request(
            url,
            maximum=self._limits.max_body_bytes,
            delay=delay,
            allowed_site=allowed_site,
        )
        if not 200 <= response.status_code < 300:
            raise ValueError("page returned non-success status")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if media_type not in _HTML_MEDIA_TYPES:
            raise ValueError("unsupported page content type")
        encoding = response.encoding or "utf-8"
        try:
            text = body.decode(encoding, errors="replace")
        except LookupError:
            raise ValueError("unsupported page encoding") from None
        return extract_page(final_url, text)

    async def crawl(self, start_url: str) -> CrawlResult:
        """Crawl from *start_url* within deterministic resource and site boundaries."""

        try:
            start = normalize_url(start_url)
            resolve_and_validate(urlsplit(start).hostname or "", self._resolver)
        except UnsafeTarget:
            return CrawlResult(str(start_url).partition("?")[0], (), (CrawlFailure("", "unsafe crawl target"),), False)
        site = _registrable_domain(start, self._extractor)
        frontier: list[tuple[int, int, str]] = [(_priority(start), 0, start)]
        queued = {start}
        attempted: set[str] = set()
        policies: dict[str, RobotsPolicy] = {}
        pages: list[ExtractedPage] = []
        page_urls: set[str] = set()
        failures: list[CrawlFailure] = []
        attempts = 0

        while frontier and attempts < self._limits.max_pages:
            _rank, depth, url = heapq.heappop(frontier)
            if url in attempted:
                continue
            attempted.add(url)
            attempts += 1
            origin = _origin(url)
            policy = policies.get(origin)
            if policy is None:
                policy = await self._robots_policy(origin)
                policies[origin] = policy
            if policy.fetch_outcome is RobotsFetchOutcome.UNREACHABLE:
                failures.append(CrawlFailure(url, "robots policy unreachable"))
                continue
            try:
                allowed = policy.allowed(_USER_AGENT, url)
            except UnsafeTarget:
                allowed = False
            if not allowed:
                failures.append(CrawlFailure(url, "robots disallowed"))
                continue
            try:
                page = await self._fetch_page(url, policy, allowed_site=site)
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, UnsafeTarget):
                failures.append(CrawlFailure(url, "page fetch failed"))
                continue
            except ValueError as error:
                reason = str(error)
                allowed_reasons = {
                    "redirect loop",
                    "redirect limit exceeded",
                    "redirect leaves origin",
                    "redirect leaves website",
                    "unsafe crawl target",
                    "response body exceeds byte limit",
                    "invalid content length",
                    "page returned non-success status",
                    "unsupported page content type",
                    "unsupported page encoding",
                }
                failures.append(CrawlFailure(url, reason if reason in allowed_reasons else "page fetch failed"))
                continue
            if page.url in page_urls:
                continue
            page_urls.add(page.url)
            pages.append(page)
            if depth >= self._limits.max_depth:
                continue
            for link in page.links:
                if len(queued) >= self._limits.max_frontier_urls:
                    break
                try:
                    canonical = normalize_url(link)
                except UnsafeTarget:
                    continue
                if _registrable_domain(canonical, self._extractor) != site or canonical in queued:
                    continue
                queued.add(canonical)
                heapq.heappush(frontier, (_priority(canonical), depth + 1, canonical))

        exhausted = bool(frontier) or len(queued) >= self._limits.max_frontier_urls
        return CrawlResult(start, tuple(pages), tuple(failures), exhausted)
