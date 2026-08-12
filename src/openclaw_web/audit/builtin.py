"""Deterministic technical checks over canonical crawl and browser observations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from openclaw_web.crawl.extract import ExtractedPage
from openclaw_web.screenshots import ScreenshotResult

_MAX_EVIDENCE_ITEMS = 8
_MAX_EVIDENCE_CHARS = 240
_SENSITIVE = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|cookie|password|secret|token)\s*[:=]\s*[^\s,;]+"
)
_CONTROLS = re.compile(r"[\x00-\x1f\x7f]+")
_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True, slots=True)
class ImageObservation:
    """One bounded image accessibility/load observation."""

    url: str
    alt_text: str | None
    failed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url:
            raise TypeError("url must be a non-empty string")
        if self.alt_text is not None and not isinstance(self.alt_text, str):
            raise TypeError("alt_text must be a string or None")
        if not isinstance(self.failed, bool):
            raise TypeError("failed must be a boolean")


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    """One observed link or asset response."""

    kind: Literal["link", "asset"]
    url: str
    status_code: int | None = None
    failed: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"link", "asset"}:
            raise ValueError("kind must be link or asset")
        if not isinstance(self.url, str) or not self.url:
            raise TypeError("url must be a non-empty string")
        if not isinstance(self.failed, bool):
            raise TypeError("failed must be a boolean")
        if self.status_code is not None and (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("status_code must be between 100 and 599")


@dataclass(frozen=True, slots=True)
class PageAuditObservation:
    """Optional typed signals augmenting an extracted page without inventing data."""

    page: ExtractedPage
    requested_url: str | None = None
    redirect_count: int | None = None
    images: tuple[ImageObservation, ...] | None = None
    resources: tuple[ResourceObservation, ...] | None = None
    mobile_viewport: bool | None = None
    latest_content_date: datetime | None = None
    body_bytes: int | None = None
    dom_nodes: int | None = None
    request_count: int | None = None
    request_bytes: int | None = None
    browser: ScreenshotResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.page, ExtractedPage):
            raise TypeError("page must be an ExtractedPage")
        if self.requested_url is not None and not isinstance(self.requested_url, str):
            raise TypeError("requested_url must be a string or None")
        if self.images is not None and (
            not isinstance(self.images, tuple)
            or not all(isinstance(item, ImageObservation) for item in self.images)
        ):
            raise TypeError("images must be a tuple of ImageObservation values or None")
        if self.resources is not None and (
            not isinstance(self.resources, tuple)
            or not all(isinstance(item, ResourceObservation) for item in self.resources)
        ):
            raise TypeError("resources must be a tuple of ResourceObservation values or None")
        if self.mobile_viewport is not None and not isinstance(self.mobile_viewport, bool):
            raise TypeError("mobile_viewport must be a boolean or None")
        for name in (
            "redirect_count",
            "body_bytes",
            "dom_nodes",
            "request_count",
            "request_bytes",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        if self.latest_content_date is not None:
            if not isinstance(self.latest_content_date, datetime):
                raise TypeError("latest_content_date must be a datetime or None")
            if self.latest_content_date.tzinfo is None:
                raise ValueError("latest_content_date must be timezone-aware")
        if self.browser is not None and not isinstance(self.browser, ScreenshotResult):
            raise TypeError("browser must be a ScreenshotResult or None")


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """Stable immutable actionable result from one built-in rule."""

    rule_id: str
    severity: Literal["P0", "P1", "P2", "P3"]
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BuiltinAuditResult:
    """Built-in findings plus explicit missing-input accounting."""

    status: Literal["complete", "partial"]
    findings: tuple[AuditFinding, ...]
    unavailable_inputs: tuple[str, ...]


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return "invalid-url"
    if parts.scheme not in {"http", "https"} or host is None:
        return "invalid-url"
    rendered_host = f"[{host}]" if ":" in host else host
    authority = rendered_host if port is None else f"{rendered_host}:{port}"
    return urlunsplit((parts.scheme, authority, parts.path, "", ""))[:_MAX_EVIDENCE_CHARS]


def _safe_text(value: object) -> str:
    text = _CONTROLS.sub(" ", str(value))
    text = _SENSITIVE.sub("[redacted]", text)
    text = re.sub(
        r"https?://[^\s]+",
        lambda match: _safe_url(match.group(0)),
        text,
    )
    return " ".join(text.split())[:_MAX_EVIDENCE_CHARS]


def _same_origin(left: str, right: str) -> bool:
    try:
        left_parts = urlsplit(left)
        right_parts = urlsplit(right)
        left_port = left_parts.port or (443 if left_parts.scheme == "https" else 80)
        right_port = right_parts.port or (443 if right_parts.scheme == "https" else 80)
    except ValueError:
        return False
    return (
        left_parts.scheme.casefold(),
        (left_parts.hostname or "").casefold(),
        left_port,
    ) == (
        right_parts.scheme.casefold(),
        (right_parts.hostname or "").casefold(),
        right_port,
    )


def _finding(
    rule_id: str,
    severity: Literal["P0", "P1", "P2", "P3"],
    *evidence: object,
) -> AuditFinding:
    clean = tuple(
        item for item in (_safe_text(value) for value in evidence[:_MAX_EVIDENCE_ITEMS]) if item
    )
    return AuditFinding(rule_id, severity, clean)


def audit_page(
    observation: ExtractedPage | PageAuditObservation,
    *,
    now: datetime | None = None,
) -> BuiltinAuditResult:
    """Evaluate only available observations and never treat absence as a pass."""

    envelope = (
        observation
        if isinstance(observation, PageAuditObservation)
        else PageAuditObservation(page=observation)
    )
    page = envelope.page
    findings: list[AuditFinding] = []
    unavailable: set[str] = set()

    requested_url = envelope.requested_url or page.url
    if urlsplit(requested_url).scheme.casefold() != "https":
        findings.append(_finding("TRANSPORT-HTTPS-MISSING", "P1", requested_url))

    if envelope.redirect_count is None:
        unavailable.add("redirects")
    elif envelope.redirect_count >= 3:
        findings.append(_finding("REDIRECT-CHAIN-LONG", "P2", envelope.redirect_count))

    if page.title is None:
        findings.append(_finding("META-TITLE-MISSING", "P2", page.url))
    if page.description is None:
        findings.append(_finding("META-DESCRIPTION-MISSING", "P2", page.url))

    heading_levels = tuple(item.level for item in page.headings)
    if 1 not in heading_levels:
        findings.append(_finding("HEADING-H1-MISSING", "P2", page.url))
    if any(level > previous + 1 for previous, level in pairwise(heading_levels)) or (
        heading_levels and heading_levels[0] > 1
    ):
        findings.append(_finding("HEADING-ORDER-SKIPPED", "P3", *heading_levels))

    if envelope.images is None:
        unavailable.add("images")
    elif envelope.images:
        alt_count = sum(bool(item.alt_text and item.alt_text.strip()) for item in envelope.images)
        coverage = alt_count / len(envelope.images)
        if coverage < 0.8:
            findings.append(
                _finding("IMAGE-ALT-COVERAGE-LOW", "P2", f"{alt_count}/{len(envelope.images)}")
            )
        failed_images = tuple(item.url for item in envelope.images if item.failed)
        if failed_images:
            findings.append(_finding("IMAGE-BROKEN", "P2", *failed_images))

    if envelope.resources is None:
        unavailable.add("resources")
    else:
        unresolved_kinds = {
            item.kind for item in envelope.resources if item.status_code is None and not item.failed
        }
        unavailable.update(f"{kind}_status" for kind in unresolved_kinds)
        broken_links = tuple(
            item.url
            for item in envelope.resources
            if item.kind == "link"
            and _same_origin(page.url, item.url)
            and (item.failed or (item.status_code or 0) >= 400)
        )
        broken_assets = tuple(
            item.url
            for item in envelope.resources
            if item.kind == "asset" and (item.failed or (item.status_code or 0) >= 400)
        )
        mixed = tuple(
            item.url
            for item in envelope.resources
            if item.kind == "asset"
            and urlsplit(page.url).scheme == "https"
            and urlsplit(item.url).scheme == "http"
        )
        if broken_links:
            findings.append(_finding("LINK-BROKEN", "P2", *broken_links))
        if broken_assets:
            findings.append(_finding("ASSET-BROKEN", "P1", *broken_assets))
        if mixed:
            findings.append(_finding("MIXED-CONTENT", "P1", *mixed))

    browser_has_cta = envelope.browser is not None and any(
        cta.visible for viewport in envelope.browser.observations for cta in viewport.ctas
    )
    if not page.ctas and not browser_has_cta:
        findings.append(_finding("CTA-MISSING", "P2", page.url))
    long_forms = tuple(form.field_count for form in page.forms if form.field_count > 10)
    if long_forms:
        findings.append(_finding("FORM-LONG", "P3", *long_forms))

    if envelope.mobile_viewport is None:
        unavailable.add("mobile_viewport")
    elif not envelope.mobile_viewport:
        findings.append(_finding("MOBILE-VIEWPORT-MISSING", "P1", page.url))

    if now is not None and now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if envelope.latest_content_date is None:
        unavailable.add("latest_content_date")
    elif now is None:
        unavailable.add("stale_reference_time")
    elif (now.astimezone(UTC) - envelope.latest_content_date.astimezone(UTC)).days > 365 * 3:
        findings.append(
            _finding("CONTENT-STALE", "P3", envelope.latest_content_date.date().isoformat())
        )

    size_rules: tuple[tuple[str, str, int | None, int, Literal["P0", "P1", "P2", "P3"]], ...] = (
        ("BODY-SIZE-LARGE", "body_bytes", envelope.body_bytes, 1_000_000, "P2"),
        ("DOM-SIZE-LARGE", "dom_nodes", envelope.dom_nodes, 1_500, "P2"),
        ("REQUEST-COUNT-LARGE", "request_count", envelope.request_count, 100, "P2"),
        ("REQUEST-SIZE-LARGE", "request_bytes", envelope.request_bytes, 5_000_000, "P2"),
    )
    for rule_id, input_name, value, threshold, severity in size_rules:
        if value is None:
            unavailable.add(input_name)
        elif value > threshold:
            findings.append(_finding(rule_id, severity, value))

    if envelope.browser is None or envelope.browser.status != "complete":
        unavailable.add("browser")
    if envelope.browser is not None:
        overflows = tuple(
            item.viewport for item in envelope.browser.observations if item.horizontal_overflow
        )
        if overflows:
            findings.append(_finding("MOBILE-HORIZONTAL-OVERFLOW", "P1", *overflows))
        if envelope.browser.console_errors:
            findings.append(
                _finding("BROWSER-CONSOLE-ERROR", "P2", *envelope.browser.console_errors)
            )
        if envelope.browser.failed_requests:
            findings.append(
                _finding(
                    "BROWSER-REQUEST-ERROR",
                    "P2",
                    *(
                        f"{item.method} {item.url}: {item.reason}"
                        for item in envelope.browser.failed_requests
                    ),
                )
            )

    findings.sort(key=lambda item: (item.rule_id, _SEVERITY_ORDER[item.severity], item.evidence))
    unavailable_inputs = tuple(sorted(unavailable))
    return BuiltinAuditResult(
        "partial" if unavailable_inputs else "complete",
        tuple(findings),
        unavailable_inputs,
    )
