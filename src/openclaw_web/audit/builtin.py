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


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    """One observed link or asset response."""

    kind: Literal["link", "asset"]
    url: str
    status_code: int | None = None
    failed: bool = False

    def __post_init__(self) -> None:
        if self.status_code is not None and not 100 <= self.status_code <= 599:
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
        for name in (
            "redirect_count",
            "body_bytes",
            "dom_nodes",
            "request_count",
            "request_bytes",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        if self.latest_content_date is not None and self.latest_content_date.tzinfo is None:
            raise ValueError("latest_content_date must be timezone-aware")


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


def _finding(
    rule_id: str,
    severity: Literal["P0", "P1", "P2", "P3"],
    *evidence: object,
) -> AuditFinding:
    clean = tuple(
        item
        for item in (_safe_text(value) for value in evidence[:_MAX_EVIDENCE_ITEMS])
        if item
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
        broken_links = tuple(
            item.url
            for item in envelope.resources
            if item.kind == "link" and (item.failed or (item.status_code or 0) >= 400)
        )
        broken_assets = tuple(
            item.url
            for item in envelope.resources
            if item.kind == "asset" and (item.failed or (item.status_code or 0) >= 400)
        )
        mixed = tuple(
            item.url
            for item in envelope.resources
            if urlsplit(page.url).scheme == "https" and urlsplit(item.url).scheme == "http"
        )
        if broken_links:
            findings.append(_finding("LINK-BROKEN", "P2", *broken_links))
        if broken_assets:
            findings.append(_finding("ASSET-BROKEN", "P1", *broken_assets))
        if mixed:
            findings.append(_finding("MIXED-CONTENT", "P1", *mixed))

    if not page.ctas:
        findings.append(_finding("CTA-MISSING", "P2", page.url))
    long_forms = tuple(form.field_count for form in page.forms if form.field_count > 10)
    if long_forms:
        findings.append(_finding("FORM-LONG", "P3", *long_forms))

    if envelope.mobile_viewport is None:
        unavailable.add("mobile_viewport")
    elif not envelope.mobile_viewport:
        findings.append(_finding("MOBILE-VIEWPORT-MISSING", "P1", page.url))

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if envelope.latest_content_date is None:
        unavailable.add("latest_content_date")
    elif (current - envelope.latest_content_date).days > 365 * 3:
        findings.append(
            _finding("CONTENT-STALE", "P3", envelope.latest_content_date.date().isoformat())
        )

    size_rules: tuple[
        tuple[str, str, int | None, int, Literal["P0", "P1", "P2", "P3"]], ...
    ] = (
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

    if envelope.browser is None:
        unavailable.add("browser")
    else:
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
                    *(f"{item.method} {item.url}: {item.reason}" for item in envelope.browser.failed_requests),
                )
            )

    findings.sort(key=lambda item: (item.rule_id, _SEVERITY_ORDER[item.severity], item.evidence))
    unavailable_inputs = tuple(sorted(unavailable))
    return BuiltinAuditResult(
        "partial" if unavailable_inputs else "complete",
        tuple(findings),
        unavailable_inputs,
    )
