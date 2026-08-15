from __future__ import annotations

import asyncio
import dataclasses
import inspect
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from openclaw_web.screenshots.playwright_runner import (
    MAX_OBSERVATIONS,
    ScreenshotRunner,
)

EventHandler = Callable[[Any], object]


async def _dispatch(handler: EventHandler, value: object) -> None:
    result = handler(value)
    if inspect.isawaitable(result):
        await result


class FakeConsoleMessage:
    type = "error"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeRequest:
    def __init__(self, url: str, method: str = "GET") -> None:
        self.url = url
        self.method = method

    def failure(self) -> str:
        return "token=super-secret network failure"


class FakeRoute:
    def __init__(self, request: FakeRequest) -> None:
        self.request = request
        self.action: str | None = None
        self.url: str | None = None

    async def abort(self, _reason: str = "blockedbyclient") -> None:
        self.action = "abort"

    async def continue_(self, *, url: str | None = None) -> None:
        self.action = "continue"
        self.url = url


class FakeWebSocketRoute:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False
        self.connected = False

    async def close(self) -> None:
        self.closed = True

    def connect_to_server(self) -> None:
        self.connected = True


class Closable:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class Cancelable:
    def __init__(self) -> None:
        self.cancelled = False

    async def cancel(self) -> None:
        self.cancelled = True


class Dismissable:
    def __init__(self) -> None:
        self.dismissed = False

    async def dismiss(self) -> None:
        self.dismissed = True


class FakePage:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.viewport: dict[str, int] = {}
        self.handlers: dict[str, list[EventHandler]] = {}
        self.route_handler: EventHandler | None = None
        self.closed = False

    def on(self, event: str, handler: EventHandler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    async def route(self, _pattern: str, handler: EventHandler) -> None:
        self.route_handler = handler

    async def set_viewport_size(self, viewport: dict[str, int]) -> None:
        self.viewport = viewport

    async def goto(self, url: str, **_kwargs: object) -> None:
        if self.context.cancel:
            raise asyncio.CancelledError
        if self.context.timeout:
            raise TimeoutError("credential=private timeout")
        assert self.route_handler is not None
        route = FakeRoute(FakeRequest(url))
        await _dispatch(self.route_handler, route)
        if route.action == "abort":
            raise RuntimeError("navigation blocked")
        if self.context.websocket_url is not None:
            assert self.context.websocket_handler is not None
            websocket_route = FakeWebSocketRoute(self.context.websocket_url)
            self.context.websocket_routes.append(websocket_route)
            await _dispatch(self.context.websocket_handler, websocket_route)

        popup, download, dialog = Closable(), Cancelable(), Dismissable()
        for event, value in (("popup", popup), ("download", download), ("dialog", dialog)):
            for handler in self.handlers.get(event, []):
                await _dispatch(handler, value)
        self.context.interactions.append((popup, download, dialog))

        for index in range(MAX_OBSERVATIONS + 4):
            message = FakeConsoleMessage(
                f"error {index} https://user:pass@example.test/a?token=super-secret"
            )
            request = FakeRequest(f"https://example.test/missing/{index}?api_key=super-secret")
            for handler in self.handlers.get("console", []):
                await _dispatch(handler, message)
            for handler in self.handlers.get("requestfailed", []):
                await _dispatch(handler, request)

    async def evaluate(self, expression: str) -> object:
        if "scrollWidth" in expression and "clientWidth" in expression:
            return {
                "horizontalOverflow": self.viewport["width"] == 390,
                "documentWidth": self.viewport["width"] + 42,
                "documentHeight": 20_000,
                "ctas": [{"text": "Book now", "tag": "a", "href": "/book", "visible": True}],
                "obstructions": [{"kind": "cookie", "text": "Cookie settings", "coverage": 0.31}],
            }
        return None

    async def screenshot(self, *, path: str, **kwargs: object) -> None:
        viewport_name = {1440: "desktop", 1024: "tablet", 390: "mobile"}[self.viewport["width"]]
        if viewport_name in self.context.fail_viewports:
            raise RuntimeError("password=hunter2 screenshot failed")
        destination = Path(path)
        destination.write_bytes(b"PNG" + repr(kwargs).encode("ascii"))

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(
        self,
        *,
        fail_viewports: frozenset[str] = frozenset(),
        timeout: bool = False,
        cancel: bool = False,
        websocket_url: str | None = None,
    ) -> None:
        self.fail_viewports = fail_viewports
        self.timeout = timeout
        self.cancel = cancel
        self.websocket_url = websocket_url
        self.websocket_handler: EventHandler | None = None
        self.websocket_routes: list[FakeWebSocketRoute] = []
        self.pages: list[FakePage] = []
        self.interactions: list[tuple[Closable, Cancelable, Dismissable]] = []
        self.init_scripts: list[str] = []
        self.closed = False

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def route_web_socket(self, _pattern: str, handler: EventHandler) -> None:
        self.websocket_handler = handler

    async def new_page(self) -> FakePage:
        page = FakePage(self)
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True


class FakeLegacyContext:
    def __init__(self) -> None:
        self.init_scripts: list[str] = []
        self.page_creations = 0
        self.closed = False

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def new_page(self) -> FakePage:
        self.page_creations += 1
        raise AssertionError("page creation must remain unreachable without WebSocket routing")

    async def close(self) -> None:
        self.closed = True


class FakeLegacyBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeLegacyContext] = []
        self.closed = False

    async def new_context(self, **_kwargs: object) -> FakeLegacyContext:
        context = FakeLegacyContext()
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(
        self,
        attempts: list[frozenset[str]] | None = None,
        *,
        timeout: bool = False,
        cancel: bool = False,
        websocket_url: str | None = None,
    ) -> None:
        self.attempts = attempts or [frozenset()]
        self.timeout = timeout
        self.cancel = cancel
        self.websocket_url = websocket_url
        self.contexts: list[FakeContext] = []
        self.context_options: list[dict[str, object]] = []
        self.closed = False

    async def new_context(self, **kwargs: object) -> FakeContext:
        index = len(self.contexts)
        failures = self.attempts[min(index, len(self.attempts) - 1)]
        context = FakeContext(
            fail_viewports=failures,
            timeout=self.timeout,
            cancel=self.cancel,
            websocket_url=self.websocket_url,
        )
        self.contexts.append(context)
        self.context_options.append(kwargs)
        return context

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_captures_exact_viewports_in_deterministic_order_without_interactions(
    tmp_path: Path,
) -> None:
    browser = FakeBrowser()
    result = await ScreenshotRunner(
        tmp_path,
        browser=browser,
        local_test_mode=True,
    ).capture("http://127.0.0.1:8765/form?secret=hidden")

    assert [(shot.viewport, shot.kind) for shot in result.screenshots] == [
        ("desktop", "viewport"),
        ("desktop", "full-page"),
        ("tablet", "viewport"),
        ("tablet", "full-page"),
        ("mobile", "viewport"),
        ("mobile", "full-page"),
    ]
    assert [
        (page.viewport["width"], page.viewport["height"]) for page in browser.contexts[0].pages
    ] == [
        (1440, 1000),
        (1024, 900),
        (390, 844),
    ]
    assert browser.context_options == [
        {"accept_downloads": False, "permissions": [], "service_workers": "block"}
    ]
    assert browser.contexts[0].init_scripts
    assert 'Object.defineProperty(window, "open"' in browser.contexts[0].init_scripts[0]
    assert "HTMLFormElement.prototype" in browser.contexts[0].init_scripts[0]
    assert 'disableFormMethod("submit")' in browser.contexts[0].init_scripts[0]
    assert 'disableFormMethod("requestSubmit")' in browser.contexts[0].init_scripts[0]
    assert all(
        shot.path.exists() and shot.path.parent == tmp_path.resolve() for shot in result.screenshots
    )
    assert all("secret" not in shot.path.name for shot in result.screenshots)
    assert all(
        popup.closed and download.cancelled and dialog.dismissed
        for popup, download, dialog in browser.contexts[0].interactions
    )
    assert browser.contexts[0].closed
    assert not browser.closed


@pytest.mark.asyncio
async def test_retries_in_a_brand_new_context_and_preserves_partial_success(
    tmp_path: Path,
) -> None:
    browser = FakeBrowser([frozenset({"mobile"}), frozenset({"mobile"})])
    result = await ScreenshotRunner(tmp_path, browser=browser, local_test_mode=True).capture(
        "http://127.0.0.1:8765/"
    )

    assert result.status == "partial"
    assert result.attempts == 2
    assert len(browser.contexts) == 2
    assert all(context.closed for context in browser.contexts)
    assert {(shot.viewport, shot.kind) for shot in result.screenshots} == {
        ("desktop", "viewport"),
        ("desktop", "full-page"),
        ("tablet", "viewport"),
        ("tablet", "full-page"),
    }
    assert result.failures and all("hunter2" not in item.reason for item in result.failures)


@pytest.mark.asyncio
async def test_observations_are_immutable_sanitized_and_bounded(tmp_path: Path) -> None:
    result = await ScreenshotRunner(tmp_path, browser=FakeBrowser(), local_test_mode=True).capture(
        "http://127.0.0.1:8765/"
    )

    assert len(result.console_errors) == MAX_OBSERVATIONS
    assert len(result.failed_requests) == MAX_OBSERVATIONS
    rendered = repr((result.console_errors, result.failed_requests))
    assert "super-secret" not in rendered
    assert "user:pass" not in rendered
    assert "api_key" not in rendered
    assert isinstance(result.console_errors, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "partial"  # type: ignore[misc]
    mobile = next(item for item in result.observations if item.viewport == "mobile")
    assert mobile.horizontal_overflow
    assert mobile.document_height == 10_000
    assert mobile.ctas[0].text == "Book now"
    assert mobile.obstructions[0].kind == "cookie"


@pytest.mark.asyncio
async def test_route_blocks_mutating_requests_and_requires_url_policy(tmp_path: Path) -> None:
    validated: list[str] = []

    async def validator(_source_url: str | None, url: str) -> str:
        validated.append(url)
        return url

    browser = FakeBrowser()
    await ScreenshotRunner(tmp_path, browser=browser, url_validator=validator).capture(
        "https://example.test/"
    )
    handler = browser.contexts[0].pages[0].route_handler
    assert handler is not None
    post = FakeRoute(FakeRequest("https://example.test/submit", method="POST"))
    await _dispatch(handler, post)
    cross_origin = FakeRoute(FakeRequest("https://cdn.example.test/image.png"))
    await _dispatch(handler, cross_origin)

    assert post.action == "abort"
    assert cross_origin.action == "continue"
    assert "https://cdn.example.test/image.png" in validated
    with pytest.raises(ValueError, match="URL validator"):
        ScreenshotRunner(tmp_path / "unsafe", browser=browser)


@pytest.mark.asyncio
async def test_websocket_handshake_is_blocked_before_connecting_and_sanitized(
    tmp_path: Path,
) -> None:
    unsafe_url = "ws://user:password@external.invalid/private?token=super-secret"
    validated: list[str] = []

    async def validator(_source_url: str | None, url: str) -> str:
        validated.append(url)
        return url

    browser = FakeBrowser(websocket_url=unsafe_url)
    result = await ScreenshotRunner(
        tmp_path,
        browser=browser,
        url_validator=validator,
    ).capture("https://example.test/")

    routes = [route for context in browser.contexts for route in context.websocket_routes]
    assert routes
    assert all(route.closed and not route.connected for route in routes)
    assert unsafe_url not in validated
    websocket_failures = [item for item in result.failed_requests if item.method == "WEBSOCKET"]
    assert websocket_failures
    assert websocket_failures[0].url == "ws://external.invalid/private"
    assert websocket_failures[0].reason == "blocked by screenshot policy"
    assert "password" not in repr(websocket_failures)
    assert "super-secret" not in repr(websocket_failures)


@pytest.mark.asyncio
async def test_missing_native_websocket_policy_fails_closed_before_page_creation(
    tmp_path: Path,
) -> None:
    browser = FakeLegacyBrowser()

    async def factory() -> FakeLegacyBrowser:
        return browser

    async def validator(_source_url: str | None, url: str) -> str:
        return url

    result = await ScreenshotRunner(
        tmp_path,
        browser_factory=factory,
        url_validator=validator,
    ).capture("http://user:password@127.0.0.1:8765/private?token=super-secret")

    assert result.status == "partial"
    assert result.attempts == 2
    assert result.screenshots == ()
    assert result.observations == ()
    assert result.console_errors == ()
    assert result.failed_requests == ()
    assert len(result.failures) == 2
    assert all(failure.phase == "attempt" for failure in result.failures)
    assert all("WebSocket routing capability" in failure.reason for failure in result.failures)
    assert all(len(failure.reason) <= 500 for failure in result.failures)
    assert "password" not in repr(result)
    assert "super-secret" not in repr(result)
    assert len(browser.contexts) == 2
    assert all(context.page_creations == 0 for context in browser.contexts)
    assert all(context.init_scripts == [] for context in browser.contexts)
    assert all(context.closed for context in browser.contexts)
    assert browser.closed


@pytest.mark.asyncio
async def test_output_paths_cannot_escape_owned_root(tmp_path: Path) -> None:
    output = tmp_path / "evidence"
    browser = FakeBrowser()
    result = await ScreenshotRunner(output, browser=browser, local_test_mode=True).capture(
        "http://127.0.0.1:8765/../../outside?filename=../../escape"
    )

    root = output.resolve()
    assert result.screenshots
    assert all(shot.path.is_relative_to(root) for shot in result.screenshots)
    assert not (tmp_path / "escape.png").exists()


@pytest.mark.asyncio
async def test_timeout_and_cancellation_close_every_owned_resource(tmp_path: Path) -> None:
    timeout_browser = FakeBrowser(timeout=True)

    async def factory() -> FakeBrowser:
        return timeout_browser

    result = await ScreenshotRunner(
        tmp_path / "timeout", browser_factory=factory, local_test_mode=True
    ).capture("http://127.0.0.1:8765/")
    assert result.status == "partial"
    assert len(timeout_browser.contexts) == 2
    assert all(context.closed for context in timeout_browser.contexts)
    assert timeout_browser.closed

    cancelled_browser = FakeBrowser(cancel=True)

    async def cancelled_factory() -> FakeBrowser:
        return cancelled_browser

    with pytest.raises(asyncio.CancelledError):
        await ScreenshotRunner(
            tmp_path / "cancel", browser_factory=cancelled_factory, local_test_mode=True
        ).capture("http://127.0.0.1:8765/")
    assert cancelled_browser.contexts[0].closed
    assert cancelled_browser.closed


class _FixtureHandler(BaseHTTPRequestHandler):
    submissions = 0
    websocket_handshakes = 0

    def do_GET(self) -> None:
        if self.headers.get("Upgrade", "").casefold() == "websocket":
            type(self).websocket_handshakes += 1
            self.send_error(400)
            return
        body = b"""<!doctype html><html><body>
        <a href='/book'>Book now</a><form method='post'><input name='email'><button>Send</button></form>
        <script>
        const socketUrl = `ws://${location.host}/socket?token=fixture-secret`;
        try { new WebSocket(socketUrl); } catch (_) {}
        </script>
        </body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        type(self).submissions += 1
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.mark.asyncio
async def test_real_chromium_local_fixture_smoke(tmp_path: Path) -> None:
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    try:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except Exception as error:  # noqa: BLE001 - executable availability varies by host.
            pytest.skip(f"Chromium capability unavailable: {type(error).__name__}")
    finally:
        await playwright.stop()

    await browser.close()
    _FixtureHandler.submissions = 0
    _FixtureHandler.websocket_handshakes = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/form"
        result = await ScreenshotRunner(tmp_path, local_test_mode=True).capture(url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.status == "complete", result.failures
    assert len(result.screenshots) == 6
    assert _FixtureHandler.submissions == 0
    assert _FixtureHandler.websocket_handshakes == 0
    websocket_failures = [item for item in result.failed_requests if item.method == "WEBSOCKET"]
    assert websocket_failures
    assert all(item.url.endswith("/socket") for item in websocket_failures)
    assert "fixture-secret" not in repr(websocket_failures)
