from __future__ import annotations

from openclaw_web.audit.builtin import PageAuditObservation, ResourceObservation, audit_page
from openclaw_web.crawl.extract import ExtractedPage
from openclaw_web.crawl.service import CrawlFailure, CrawlResult
from openclaw_web.pipeline.production import _page_resource_observations, _scoring_inputs
from openclaw_web.scoring.rules import parse_rubric


def _page(*, url: str, links: tuple[str, ...] = ()) -> ExtractedPage:
    return ExtractedPage(
        url=url,
        title="Example",
        description="Example",
        headings=(),
        ctas=(),
        forms=(),
        links=links,
        evidence=(),
    )


def test_production_resource_observations_preserve_observed_and_unknown_links() -> None:
    root = _page(
        url="https://example.com/",
        links=(
            "https://example.com/about",
            "https://example.com/missing",
            "https://example.com/not-crawled",
            "https://external.example/info",
        ),
    )
    about = _page(url="https://example.com/about")
    crawl = CrawlResult(
        start_url=root.url,
        pages=(root, about),
        failures=(
            CrawlFailure(
                "https://example.com/missing",
                "page returned non-success status",
                status_code=404,
            ),
        ),
        budget_exhausted=False,
    )

    observations = _page_resource_observations(root, crawl)

    assert observations == (
        ResourceObservation("link", "https://example.com/about", status_code=200),
        ResourceObservation("link", "https://example.com/missing", status_code=404),
        ResourceObservation("link", "https://example.com/not-crawled"),
    )


def test_scoring_does_not_supply_broken_links_when_a_link_status_is_unknown() -> None:
    page = _page(
        url="https://example.com/",
        links=("https://example.com/not-crawled",),
    )
    audit = audit_page(
        PageAuditObservation(
            page=page,
            resources=(ResourceObservation("link", "https://example.com/not-crawled"),),
        )
    )
    rubric = parse_rubric(
        """
rubric_version: test-v1
maximum_score: 100
qualification:
  threshold: 1
  provisional: false
allowed_inputs:
  - audit.broken_links
base_rules:
  - rule_id: broken-links
    input: audit.broken_links
    operator: gt
    threshold: 0
    points: 10
    evidence_ids: [LINK-BROKEN]
cohort_overrides:
  other:
    rules: []
"""
    )

    inputs = _scoring_inputs(rubric, CrawlResult(page.url, (page,), (), False), (audit,), None)

    assert "broken_links" not in inputs.get("audit", {})


def test_scoring_supplies_broken_links_only_after_all_link_statuses_are_observed() -> None:
    page = _page(url="https://example.com/", links=("https://example.com/missing",))
    audit = audit_page(
        PageAuditObservation(
            page=page,
            resources=(ResourceObservation("link", "https://example.com/missing", 404),),
        )
    )
    rubric = parse_rubric(
        """
rubric_version: test-v1
maximum_score: 100
qualification:
  threshold: 1
  provisional: false
allowed_inputs:
  - audit.broken_links
base_rules:
  - rule_id: broken-links
    input: audit.broken_links
    operator: gt
    threshold: 0
    points: 10
    evidence_ids: [LINK-BROKEN]
cohort_overrides:
  other:
    rules: []
"""
    )

    inputs = _scoring_inputs(rubric, CrawlResult(page.url, (page,), (), False), (audit,), None)

    assert inputs["audit"]["broken_links"] == 1
