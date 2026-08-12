from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openclaw_web.audit.builtin import (
    ImageObservation,
    PageAuditObservation,
    ResourceObservation,
    audit_page,
)
from openclaw_web.crawl.extract import (
    CallToAction,
    ExtractedPage,
    FormSummary,
    Heading,
)
from openclaw_web.screenshots import BrowserObservation, FailedRequest, ScreenshotResult


def _page(**changes: object) -> ExtractedPage:
    values: dict[str, object] = {
        "url": "https://example.com/",
        "title": "Example",
        "description": "A useful description",
        "headings": (Heading(1, "Example"), Heading(2, "Services")),
        "ctas": (CallToAction("Contact", "contact", "https://example.com/contact"),),
        "forms": (),
        "links": ("https://example.com/contact",),
        "evidence": (),
    }
    values.update(changes)
    return ExtractedPage(**values)  # type: ignore[arg-type]


def _browser_result() -> ScreenshotResult:
    return ScreenshotResult(
        source_url="https://example.com/",
        status="complete",
        attempts=1,
        screenshots=(),
        observations=(
            BrowserObservation(
                viewport="mobile",
                horizontal_overflow=False,
                document_width=390,
                document_height=900,
                ctas=(),
                obstructions=(),
            ),
        ),
        console_errors=("token=secret\x00 exploded " + "x" * 500,),
        failed_requests=(
            FailedRequest("GET", "https://user:pass@example.com/app.js?key=secret", "failed"),
        ),
        failures=(),
    )


def test_builtin_audit_flags_semantic_conversion_and_transport_problems() -> None:
    page = _page(
        url="http://example.com/",
        title=None,
        description=None,
        headings=(Heading(2, "Skipped"),),
        ctas=(),
        forms=(FormSummary(None, "post", 12, tuple(f"field-{i}" for i in range(12))),),
    )

    result = audit_page(page)

    assert {finding.rule_id for finding in result.findings} >= {
        "TRANSPORT-HTTPS-MISSING",
        "META-TITLE-MISSING",
        "META-DESCRIPTION-MISSING",
        "HEADING-H1-MISSING",
        "HEADING-ORDER-SKIPPED",
        "CTA-MISSING",
        "FORM-LONG",
    }
    assert result.status == "partial"
    assert "images" in result.unavailable_inputs


def test_builtin_audit_uses_only_observed_resource_and_size_signals() -> None:
    observation = PageAuditObservation(
        page=_page(),
        requested_url="http://example.com/",
        redirect_count=4,
        images=(ImageObservation("https://example.com/hero.jpg", alt_text=None, failed=False),),
        resources=(
            ResourceObservation("link", "https://example.com/missing", status_code=404),
            ResourceObservation("asset", "https://example.com/app.js", failed=True),
            ResourceObservation("asset", "http://cdn.example.com/old.js", status_code=200),
        ),
        mobile_viewport=True,
        latest_content_date=datetime(2020, 1, 1, tzinfo=UTC),
        body_bytes=1_200_000,
        dom_nodes=3_000,
        request_count=130,
        request_bytes=6_000_000,
        browser=_browser_result(),
    )

    result = audit_page(observation, now=datetime(2026, 8, 13, tzinfo=UTC))

    assert [finding.rule_id for finding in result.findings] == sorted(
        finding.rule_id for finding in result.findings
    )
    assert {finding.rule_id for finding in result.findings} >= {
        "REDIRECT-CHAIN-LONG",
        "IMAGE-ALT-COVERAGE-LOW",
        "LINK-BROKEN",
        "ASSET-BROKEN",
        "MIXED-CONTENT",
        "CONTENT-STALE",
        "BODY-SIZE-LARGE",
        "DOM-SIZE-LARGE",
        "REQUEST-COUNT-LARGE",
        "REQUEST-SIZE-LARGE",
        "BROWSER-CONSOLE-ERROR",
        "BROWSER-REQUEST-ERROR",
    }
    assert result.status == "complete"


def test_builtin_audit_distinguishes_unavailable_inputs_from_passing_checks() -> None:
    result = audit_page(_page())

    assert result.findings == ()
    assert result.status == "partial"
    assert set(result.unavailable_inputs) >= {
        "redirects",
        "images",
        "resources",
        "mobile_viewport",
        "latest_content_date",
        "body_bytes",
        "dom_nodes",
        "request_count",
        "request_bytes",
        "browser",
    }


def test_builtin_findings_are_frozen_bounded_sanitized_and_deterministic() -> None:
    observation = PageAuditObservation(page=_page(), browser=_browser_result())

    first = audit_page(observation)
    second = audit_page(observation)

    assert first == second
    finding = next(item for item in first.findings if item.rule_id == "BROWSER-CONSOLE-ERROR")
    assert len(finding.evidence) <= 8
    assert all(len(item) <= 240 for item in finding.evidence)
    assert all("secret" not in item and "pass" not in item for item in finding.evidence)
    with pytest.raises((AttributeError, TypeError)):
        finding.evidence += ("changed",)


def test_builtin_observations_reject_invalid_counts() -> None:
    with pytest.raises(ValueError):
        PageAuditObservation(page=_page(), redirect_count=-1)

