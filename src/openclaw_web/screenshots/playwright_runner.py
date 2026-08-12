"""Safe, deterministic Playwright screenshot orchestration.

Production callers must inject ``url_validator``. It is invoked for the initial
navigation and every HTTP(S) browser request and must enforce the crawler's URL,
DNS, and redirect policy before returning the exact canonical URL Playwright may
request. WebSocket handshakes are blocked before connection. ``local_test_mode``
is deliberately limited to loopback HTTP fixtures.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import os
import re
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias
from urllib.parse import urlsplit, urlunsplit

MAX_OBSERVATIONS = 50
_MAX_TEXT_LENGTH = 500
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE", "CONNECT"})
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|cookie|credential|password|secret|token)\s*="
    r"\s*[^\s&;,]+"
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
_WEB_URL_IN_TEXT = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Viewport:
    """One exact responsive capture size."""

    name: Literal["desktop", "tablet", "mobile"]
    width: int
    height: int


VIEWPORTS: tuple[Viewport, ...] = (
    Viewport("desktop", 1440, 1000),
    Viewport("tablet", 1024, 900),
    Viewport("mobile", 390, 844),
)


@dataclass(frozen=True, slots=True)
class ScreenshotLimits:
    """Hard time, image, and observation budgets for one capture."""

    navigation_timeout_seconds: float = 20.0
    attempt_timeout_seconds: float = 90.0
    max_full_page_height: int = 10_000
    max_image_bytes: int = 20 * 1024 * 1024
    max_observations: int = MAX_OBSERVATIONS

    def __post_init__(self) -> None:
        timeouts = (self.navigation_timeout_seconds, self.attempt_timeout_seconds)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            or value > 300
            for value in timeouts
        ):
            raise ValueError("screenshot timeouts must be finite and between 0 and 300 seconds")
        integers = (self.max_full_page_height, self.max_image_bytes, self.max_observations)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in integers
        ):
            raise ValueError("screenshot limits must be positive integers")
        if self.max_full_page_height > 20_000:
            raise ValueError("full-page height cannot exceed 20000 pixels")
        if self.max_image_bytes > 50 * 1024 * 1024:
            raise ValueError("image size cannot exceed 50 MiB")
        if self.max_observations > 200:
            raise ValueError("observation count cannot exceed 200")


@dataclass(frozen=True, slots=True)
class ScreenshotRecord:
    """One atomically persisted screenshot."""

    viewport: str
    kind: Literal["viewport", "full-page"]
    width: int
    height: int
    path: Path
    byte_size: int


@dataclass(frozen=True, slots=True)
class CallToActionObservation:
    """A visible, non-interacted CTA candidate."""

    text: str
    tag: str
    href: str | None
    visible: bool


@dataclass(frozen=True, slots=True)
class ObstructionObservation:
    """A fixed or modal element that may obstruct page content."""

    kind: str
    text: str
    coverage: float


@dataclass(frozen=True, slots=True)
class BrowserObservation:
    """Bounded deterministic DOM observations for one viewport."""

    viewport: str
    horizontal_overflow: bool
    document_width: int
    document_height: int
    ctas: tuple[CallToActionObservation, ...]
    obstructions: tuple[ObstructionObservation, ...]


@dataclass(frozen=True, slots=True)
class FailedRequest:
    """One sanitized failed browser request."""

    method: str
    url: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScreenshotFailure:
    """One sanitized attempt failure."""

    attempt: int
    viewport: str | None
    phase: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScreenshotResult:
    """Immutable complete or partial responsive capture result."""

    source_url: str
    status: Literal["complete", "partial"]
    attempts: int
    screenshots: tuple[ScreenshotRecord, ...]
    observations: tuple[BrowserObservation, ...]
    console_errors: tuple[str, ...]
    failed_requests: tuple[FailedRequest, ...]
    failures: tuple[ScreenshotFailure, ...]


ValidatorResult: TypeAlias = str | Awaitable[str]
UrlValidator: TypeAlias = Callable[[str | None, str], ValidatorResult]
BrowserFactory: TypeAlias = Callable[[], Any | Awaitable[Any]]


def _sanitize_text(value: object) -> str:
    text = _CONTROL_CHARACTERS.sub(" ", str(value))
    text = _WEB_URL_IN_TEXT.sub(lambda match: _sanitize_url(match.group(0)), text)
    text = _SENSITIVE_ASSIGNMENT.sub("[redacted]", text)
    return text[:_MAX_TEXT_LENGTH]


def _sanitize_url(value: object) -> str:
    return _sanitize_network_url(value, frozenset({"http", "https"}))


def _sanitize_websocket_url(value: object) -> str:
    return _sanitize_network_url(value, frozenset({"ws", "wss"}))


def _sanitize_network_url(value: object, schemes: frozenset[str]) -> str:
    try:
        parts = urlsplit(str(value))
        host = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return ""
    if parts.scheme not in schemes or host is None:
        return ""
    rendered_host = f"[{host}]" if ":" in host else host
    authority = rendered_host if port is None else f"{rendered_host}:{port}"
    return urlunsplit((parts.scheme, authority, parts.path, "", ""))[:_MAX_TEXT_LENGTH]


def _path_within(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError("screenshot path escapes the output directory") from None
    return resolved


def _extract_value(value: object, name: str, default: object = "") -> object:
    item = getattr(value, name, default)
    return item() if callable(item) else item


class ScreenshotRunner:
    """Capture three exact viewports without interacting with page controls."""

    def __init__(
        self,
        output_dir: Path,
        *,
        browser: Any | None = None,
        browser_factory: BrowserFactory | None = None,
        url_validator: UrlValidator | None = None,
        local_test_mode: bool = False,
        limits: ScreenshotLimits | None = None,
    ) -> None:
        if browser is not None and browser_factory is not None:
            raise ValueError("browser and browser_factory are mutually exclusive")
        if url_validator is None and not local_test_mode:
            raise ValueError("a URL validator is required outside explicit local test mode")
        self._output_dir = output_dir.resolve()
        self._browser = browser
        self._browser_factory = browser_factory
        self._url_validator = url_validator
        self._local_test_mode = local_test_mode
        self._limits = limits or ScreenshotLimits()

    async def capture(self, url: str) -> ScreenshotResult:
        """Capture responsive evidence, retrying once in a brand-new context."""

        self._output_dir.mkdir(parents=True, exist_ok=True)
        if not self._output_dir.is_dir():
            raise ValueError("screenshot output must be a directory")
        canonical_url = await self._validate_request(None, url)
        browser, cleanup = await self._acquire_browser()
        screenshots: dict[tuple[str, str], ScreenshotRecord] = {}
        observations: dict[str, BrowserObservation] = {}
        console_errors: list[str] = []
        failed_requests: list[FailedRequest] = []
        failures: list[ScreenshotFailure] = []
        attempts = 0
        try:
            for attempt in (1, 2):
                attempts = attempt
                attempt_failed = await self._capture_attempt(
                    browser,
                    canonical_url,
                    attempt,
                    screenshots,
                    observations,
                    console_errors,
                    failed_requests,
                    failures,
                )
                if not attempt_failed:
                    break
        finally:
            await cleanup()

        ordered_screenshots = tuple(
            screenshots[key]
            for viewport in VIEWPORTS
            for key in ((viewport.name, "viewport"), (viewport.name, "full-page"))
            if key in screenshots
        )
        ordered_observations = tuple(
            observations[viewport.name] for viewport in VIEWPORTS if viewport.name in observations
        )
        status: Literal["complete", "partial"] = (
            "complete" if len(ordered_screenshots) == len(VIEWPORTS) * 2 else "partial"
        )
        return ScreenshotResult(
            source_url=_sanitize_url(canonical_url),
            status=status,
            attempts=attempts,
            screenshots=ordered_screenshots,
            observations=ordered_observations,
            console_errors=tuple(console_errors),
            failed_requests=tuple(failed_requests),
            failures=tuple(failures),
        )

    async def _acquire_browser(self) -> tuple[Any, Callable[[], Awaitable[None]]]:
        if self._browser is not None:

            async def no_cleanup() -> None:
                return None

            return self._browser, no_cleanup

        if self._browser_factory is not None:
            candidate = self._browser_factory()
            browser = await candidate if inspect.isawaitable(candidate) else candidate

            async def close_factory_browser() -> None:
                await browser.close()

            return browser, close_factory_browser

        from playwright.async_api import async_playwright

        runtime = await async_playwright().start()
        try:
            browser = await runtime.chromium.launch(headless=True)
        except BaseException:
            await runtime.stop()
            raise

        async def close_owned_browser() -> None:
            try:
                await browser.close()
            finally:
                await runtime.stop()

        return browser, close_owned_browser

    async def _capture_attempt(
        self,
        browser: Any,
        url: str,
        attempt: int,
        screenshots: dict[tuple[str, str], ScreenshotRecord],
        observations: dict[str, BrowserObservation],
        console_errors: list[str],
        failed_requests: list[FailedRequest],
        failures: list[ScreenshotFailure],
    ) -> bool:
        context: Any | None = None
        failed = False
        try:
            async with asyncio.timeout(self._limits.attempt_timeout_seconds):
                context = await browser.new_context(
                    accept_downloads=False,
                    permissions=[],
                    service_workers="block",
                )
                await self._install_websocket_policy(context, failed_requests)
                await context.add_init_script(_BLOCK_POPUPS_SCRIPT)
                for viewport in VIEWPORTS:
                    viewport_failed = await self._capture_viewport(
                        context,
                        url,
                        attempt,
                        viewport,
                        screenshots,
                        observations,
                        console_errors,
                        failed_requests,
                        failures,
                    )
                    failed = failed or viewport_failed
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - browser adapters share no error base.
            failures.append(ScreenshotFailure(attempt, None, "attempt", _error_reason(error)))
            failed = True
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception as error:  # noqa: BLE001 - cleanup must not hide evidence.
                    failures.append(
                        ScreenshotFailure(attempt, None, "context-close", _error_reason(error))
                    )
                    failed = True
        return failed

    async def _install_websocket_policy(
        self,
        context: Any,
        failed_requests: list[FailedRequest],
    ) -> None:
        route_web_socket = getattr(context, "route_web_socket", None)
        if not callable(route_web_socket):
            raise NotImplementedError("native WebSocket routing capability is required")

        async def block_websocket(route: Any) -> None:
            if len(failed_requests) < self._limits.max_observations:
                failed_requests.append(
                    FailedRequest(
                        method="WEBSOCKET",
                        url=_sanitize_websocket_url(_extract_value(route, "url")),
                        reason="blocked by screenshot policy",
                    )
                )
            await route.close()

        await route_web_socket("**/*", block_websocket)

    async def _capture_viewport(
        self,
        context: Any,
        url: str,
        attempt: int,
        viewport: Viewport,
        screenshots: dict[tuple[str, str], ScreenshotRecord],
        observations: dict[str, BrowserObservation],
        console_errors: list[str],
        failed_requests: list[FailedRequest],
        failures: list[ScreenshotFailure],
    ) -> bool:
        page: Any | None = None
        failed = False
        try:
            page = await context.new_page()
            await page.set_viewport_size({"width": viewport.width, "height": viewport.height})
            self._attach_handlers(page, console_errors, failed_requests)
            await page.route("**/*", self._route_handler(page, url))
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(self._limits.navigation_timeout_seconds * 1000),
            )
            raw_observation = await page.evaluate(_OBSERVATION_SCRIPT)
            observation = self._parse_observation(viewport, raw_observation)
            observations[viewport.name] = observation
            kinds: tuple[Literal["viewport", "full-page"], ...] = ("viewport", "full-page")
            for kind in kinds:
                try:
                    record = await self._capture_image(
                        page, url, attempt, viewport, kind, observation
                    )
                    screenshots[(viewport.name, kind)] = record
                except Exception as error:  # noqa: BLE001 - partial shots are retained.
                    failures.append(
                        ScreenshotFailure(
                            attempt,
                            viewport.name,
                            kind,
                            _error_reason(error),
                        )
                    )
                    failed = True
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - browser adapters share no error base.
            failures.append(
                ScreenshotFailure(
                    attempt,
                    viewport.name,
                    "navigation",
                    _error_reason(error),
                )
            )
            failed = True
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception as error:  # noqa: BLE001 - cleanup must not hide evidence.
                    failures.append(
                        ScreenshotFailure(
                            attempt,
                            viewport.name,
                            "page-close",
                            _error_reason(error),
                        )
                    )
                    failed = True
        return failed

    def _attach_handlers(
        self,
        page: Any,
        console_errors: list[str],
        failed_requests: list[FailedRequest],
    ) -> None:
        def console_handler(message: object) -> None:
            if len(console_errors) >= self._limits.max_observations:
                return
            if str(_extract_value(message, "type")).casefold() == "error":
                console_errors.append(_sanitize_text(_extract_value(message, "text")))

        def request_failed_handler(request: object) -> None:
            if len(failed_requests) >= self._limits.max_observations:
                return
            failed_requests.append(
                FailedRequest(
                    method=_sanitize_text(_extract_value(request, "method")),
                    url=_sanitize_url(_extract_value(request, "url")),
                    reason=_sanitize_text(_extract_value(request, "failure")),
                )
            )

        async def close_popup(popup: Any) -> None:
            await popup.close()

        async def cancel_download(download: Any) -> None:
            await download.cancel()

        async def dismiss_dialog(dialog: Any) -> None:
            await dialog.dismiss()

        page.on("console", console_handler)
        page.on("requestfailed", request_failed_handler)
        page.on("popup", close_popup)
        page.on("download", cancel_download)
        page.on("dialog", dismiss_dialog)

    def _route_handler(self, page: Any, initial_url: str) -> Callable[[Any], Awaitable[None]]:
        async def handle(route: Any) -> None:
            request = route.request
            method = str(_extract_value(request, "method")).upper()
            if method in _MUTATING_METHODS:
                await route.abort("blockedbyclient")
                return
            target = str(_extract_value(request, "url"))
            page_url = str(getattr(page, "url", ""))
            source = initial_url if not page_url or page_url == "about:blank" else page_url
            try:
                validated = await self._validate_request(source, target)
            except Exception:  # noqa: BLE001 - unsafe requests fail closed without leaking details.
                await route.abort("blockedbyclient")
                return
            await route.continue_(url=validated)

        return handle

    async def _validate_request(self, source_url: str | None, target_url: str) -> str:
        if self._url_validator is not None:
            result = self._url_validator(source_url, target_url)
            validated = await result if inspect.isawaitable(result) else result
            if not isinstance(validated, str) or not validated:
                raise ValueError("URL validator returned an invalid URL")
            return validated
        parts = urlsplit(target_url)
        host = parts.hostname
        if (
            not self._local_test_mode
            or parts.scheme != "http"
            or host is None
            or host not in {"127.0.0.1", "::1", "localhost"}
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError("local test mode permits only credential-free loopback HTTP URLs")
        return target_url

    async def _capture_image(
        self,
        page: Any,
        url: str,
        attempt: int,
        viewport: Viewport,
        kind: Literal["viewport", "full-page"],
        observation: BrowserObservation,
    ) -> ScreenshotRecord:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        final = _path_within(
            self._output_dir,
            self._output_dir / f"{digest}-{viewport.name}-{kind}.png",
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}-{viewport.name}-{kind}.attempt-{attempt}.",
            suffix=".tmp.png",
            dir=self._output_dir,
        )
        os.close(descriptor)
        temporary = _path_within(self._output_dir, Path(temporary_name))
        height = viewport.height
        options: dict[str, object] = {
            "path": str(temporary),
            "type": "png",
            "animations": "disabled",
            "caret": "hide",
            "timeout": int(self._limits.navigation_timeout_seconds * 1000),
        }
        if kind == "full-page":
            height = min(observation.document_height, self._limits.max_full_page_height)
            options["clip"] = {
                "x": 0,
                "y": 0,
                "width": viewport.width,
                "height": max(1, height),
            }
        try:
            await page.screenshot(**options)
            size = temporary.stat().st_size
            if size <= 0 or size > self._limits.max_image_bytes:
                raise ValueError("screenshot exceeds byte budget")
            os.replace(temporary, final)
        finally:
            temporary.unlink(missing_ok=True)
        return ScreenshotRecord(viewport.name, kind, viewport.width, height, final, size)

    def _parse_observation(self, viewport: Viewport, value: object) -> BrowserObservation:
        data = value if isinstance(value, Mapping) else {}
        document_width = self._bounded_int(data.get("documentWidth"), viewport.width)
        document_height = self._bounded_int(
            data.get("documentHeight"), viewport.height, maximum=self._limits.max_full_page_height
        )
        raw_ctas = data.get("ctas")
        ctas = tuple(
            CallToActionObservation(
                text=_sanitize_text(item.get("text", "")),
                tag=_sanitize_text(item.get("tag", "")),
                href=_sanitize_url_or_path(item.get("href")),
                visible=bool(item.get("visible", False)),
            )
            for item in (raw_ctas if isinstance(raw_ctas, list) else [])[
                : self._limits.max_observations
            ]
            if isinstance(item, Mapping)
        )
        raw_obstructions = data.get("obstructions")
        obstructions = tuple(
            ObstructionObservation(
                kind=_sanitize_text(item.get("kind", "unknown")),
                text=_sanitize_text(item.get("text", "")),
                coverage=_bounded_float(item.get("coverage")),
            )
            for item in (raw_obstructions if isinstance(raw_obstructions, list) else [])[
                : self._limits.max_observations
            ]
            if isinstance(item, Mapping)
        )
        return BrowserObservation(
            viewport=viewport.name,
            horizontal_overflow=bool(data.get("horizontalOverflow", False)),
            document_width=document_width,
            document_height=document_height,
            ctas=ctas,
            obstructions=obstructions,
        )

    @staticmethod
    def _bounded_int(value: object, default: int, *, maximum: int = 20_000) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            return default
        return max(0, min(int(value), maximum))


def _sanitize_url_or_path(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("/") and not value.startswith("//"):
        return value.partition("?")[0][:_MAX_TEXT_LENGTH]
    sanitized = _sanitize_url(value)
    return sanitized or None


def _error_reason(error: Exception) -> str:
    message = _sanitize_text(error)
    return _sanitize_text(f"{type(error).__name__}: {message}" if message else type(error).__name__)


def _bounded_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return 0.0
    return max(0.0, min(float(value), 1.0))


_OBSERVATION_SCRIPT = """
() => {
  const root = document.documentElement;
  const body = document.body;
  const documentWidth = Math.max(root?.scrollWidth || 0, body?.scrollWidth || 0);
  const documentHeight = Math.max(root?.scrollHeight || 0, body?.scrollHeight || 0);
  const clientWidth = root?.clientWidth || window.innerWidth;
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      Number(style.opacity) > 0 && rect.width > 0 && rect.height > 0;
  };
  const ctas = Array.from(document.querySelectorAll("a, button, [role='button'], input[type='submit']"))
    .filter(visible)
    .slice(0, 50)
    .map((element) => ({
      text: (element.innerText || element.value || element.getAttribute("aria-label") || "").trim().slice(0, 200),
      tag: element.tagName.toLowerCase(),
      href: element.href || null,
      visible: true,
    }));
  const obstructionPattern = /cookie|consent|modal|dialog|popup|banner|overlay/i;
  const obstructions = Array.from(document.querySelectorAll("body *"))
    .filter((element) => {
      if (!visible(element)) return false;
      const style = getComputedStyle(element);
      const identity = `${element.id} ${element.className} ${element.getAttribute("role") || ""}`;
      return (style.position === "fixed" || style.position === "sticky" || element.getAttribute("aria-modal") === "true") &&
        obstructionPattern.test(identity);
    })
    .slice(0, 50)
    .map((element) => {
      const rect = element.getBoundingClientRect();
      const identity = `${element.id} ${element.className}`;
      return {
        kind: /cookie|consent/i.test(identity) ? "cookie" : "overlay",
        text: (element.innerText || element.getAttribute("aria-label") || "").trim().slice(0, 200),
        coverage: Math.min(1, Math.max(0, rect.width * rect.height / (window.innerWidth * window.innerHeight))),
      };
    });
  return {
    horizontalOverflow: documentWidth > clientWidth + 1,
    documentWidth,
    documentHeight,
    ctas,
    obstructions,
  };
}
"""

_BLOCK_POPUPS_SCRIPT = """
Object.defineProperty(window, "open", {
  configurable: false,
  writable: false,
  value: () => null,
});
const disableFormMethod = (name) => {
  try {
    Object.defineProperty(HTMLFormElement.prototype, name, {
      configurable: false,
      writable: false,
      value: () => undefined,
    });
  } catch (_) {
    // Route interception remains the fail-closed network boundary.
  }
};
disableFormMethod("submit");
disableFormMethod("requestSubmit");
"""
