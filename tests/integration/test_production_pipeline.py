from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import AnyHttpUrl

from openclaw_web.audit.builtin import AuditFinding, BuiltinAuditResult
from openclaw_web.audit.lighthouse import LighthouseMetrics, LighthouseRunResult
from openclaw_web.crawl.extract import ExtractedPage
from openclaw_web.crawl.service import CrawlResult, WebsiteCrawler
from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository
from openclaw_web.delivery.components import build_review_card
from openclaw_web.delivery.openclaw_transport import SentMessage
from openclaw_web.delivery.outbox import OutboxWorker
from openclaw_web.discovery.base import DiscoveryProviderError
from openclaw_web.discovery.scheduler import APPROVED_COHORTS
from openclaw_web.models import CandidateSeed, DeliveryRecord, DeliveryState, ProjectState
from openclaw_web.pipeline.production import (
    ProductionPipeline,
    ProductionProject,
)
from openclaw_web.scoring.rules import load_rubric, parse_rubric
from openclaw_web.screenshots import ScreenshotFailure, ScreenshotRecord, ScreenshotResult
from openclaw_web.settings import MarketCenter, MarketConfig

NOW = datetime(2026, 8, 13, 2, 0, tzinfo=UTC)
MARKET = MarketConfig(
    market_id="hanoi-80km",
    center=MarketCenter(name="Ha Noi", latitude=21.0285, longitude=105.8542),
    radius_km=80.0,
    industries=["manufacturer", "ecommerce"],
    timezone="Asia/Bangkok",
)
RUBRIC = parse_rubric(
    """
rubric_version: production-test-v1
maximum_score: 100.0
qualification:
  threshold: 10.0
  provisional: false
allowed_inputs:
  - audit.has_conversion_cta
base_rules:
  - rule_id: missing-conversion-cta
    input: audit.has_conversion_cta
    operator: eq
    threshold: false
    points: 10.0
    evidence_ids: [CTA-MISSING]
cohort_overrides:
  manufacturer:
    rules: []
  ecommerce:
    rules: []
  other:
    rules: []
"""
)


class _PeerStream:
    def get_extra_info(self, name: str) -> object:
        return ("93.184.216.34", 443) if name == "server_addr" else None


def _seed(url: str, name: str, *, latitude: float = 21.0285) -> CandidateSeed:
    return CandidateSeed(
        url=AnyHttpUrl(url),
        business_name=name,
        source_url=AnyHttpUrl("https://directory.example.org/source"),
        source_type="fixture",
        discovered_at=NOW,
        latitude=latitude,
        longitude=105.8542,
        industry_hint="manufacturer",
    )


@dataclass
class _Provider:
    name: str
    by_cohort: dict[str, tuple[CandidateSeed, ...]]
    calls: list[tuple[str, int]] = field(default_factory=list)

    def readiness(self) -> str:
        return "ready"

    async def discover(
        self, _market: MarketConfig, cohort: str, limit: int
    ) -> tuple[CandidateSeed, ...]:
        self.calls.append((cohort, limit))
        return self.by_cohort.get(cohort, ())[:limit]


@dataclass
class _FailingProvider:
    name: str = "failed-provider"

    def readiness(self) -> str:
        return "ready"

    async def discover(
        self, _market: MarketConfig, _cohort: str, _limit: int
    ) -> tuple[CandidateSeed, ...]:
        raise DiscoveryProviderError("provider request failed")


@dataclass
class _ProjectSink:
    projects: dict[str, ProductionProject] = field(default_factory=dict)

    def __call__(self, project: ProductionProject) -> bool:
        if project.project_id in self.projects:
            return False
        self.projects[project.project_id] = project
        return True


@dataclass
class _DeliverySink:
    deliveries: dict[str, DeliveryRecord] = field(default_factory=dict)

    def __call__(self, delivery: DeliveryRecord) -> bool:
        if delivery.idempotency_key in self.deliveries:
            return False
        self.deliveries[delivery.idempotency_key] = delivery
        return True


@dataclass
class _OutboxTransport:
    call_count: int = 0

    def send(self, delivery: DeliveryRecord) -> SentMessage:
        self.call_count += 1
        return SentMessage(
            "message-1",
            f"https://discord.com/channels/g/{delivery.channel_id}/message-1",
        )


async def _crawler_for_html(html_by_host: dict[str, str]) -> tuple[WebsiteCrawler, httpx.AsyncClient]:
    async def handler(request: httpx.Request) -> httpx.Response:
        html = html_by_host[str(request.url.host)]
        response = httpx.Response(
            200,
            text=html,
            headers={"Content-Type": "text/html; charset=utf-8"},
            request=request,
        )
        response.extensions["network_stream"] = _PeerStream()
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebsiteCrawler(client=client, resolver=lambda _host: ("93.184.216.34",)), client


@pytest.mark.asyncio
async def test_production_run_posts_one_defensible_candidate_idempotently(tmp_path: Path) -> None:
    database = connect(tmp_path / "state.sqlite")
    migrate(database)
    repository = Repository(database)
    provider_a = _Provider(
        "provider-a",
        {
            "manufacturer": (
                _seed("https://outside-manufacturer.com/", "Outside", latitude=10.0),
                _seed("https://good-manufacturer.com/", "Good Manufacturing"),
            )
        },
    )
    provider_b = _Provider(
        "provider-b",
        {"manufacturer": (_seed("https://weak-manufacturer.com/", "Weak Manufacturing"),)},
    )
    crawler, client = await _crawler_for_html(
        {
            "good-manufacturer.com": "<html><head><title>Good</title><meta name='description' content='Factory'></head><body><h1>Factory</h1><a href='/about'>About</a></body></html>",
            "weak-manufacturer.com": "<html><head><title>Weak</title><meta name='description' content='Factory'></head><body><h1>Factory</h1><a href='/contact'>Contact us</a></body></html>",
        }
    )
    projects = _ProjectSink()
    deliveries = _DeliverySink()
    artifact_root = (tmp_path / "artifacts").resolve()
    pipeline = ProductionPipeline(
        repository=repository,
        artifact_root=artifact_root,
        market=MARKET,
        providers=(provider_a, provider_b),
        crawler=crawler.crawl,
        rubric=RUBRIC,
        review_channel="review-channel",
        clock=lambda: NOW,
        project_sink=projects,
        delivery_sink=deliveries,
        max_discovered=6,
        per_provider_limit=2,
    )
    try:
        first = await pipeline.run()
        second = await pipeline.run()
        persisted_candidates = repository.count_candidates()
        project = next(iter(projects.projects.values()))
        delivery = next(iter(deliveries.deliveries.values()))
        database.execute(
            """
            INSERT INTO projects (
                project_id, candidate_id, state, state_version, snapshot_json
            ) VALUES (?, ?, 'review', 0, '{}')
            """,
            (project.project_id, project.candidate_id),
        )
        repository.enqueue_delivery(delivery)
        dispatched = OutboxWorker(repository, _OutboxTransport()).dispatch_once()
        component = repository.get_component_set("review-channel", "message-1")
    finally:
        await client.aclose()
        repository.close()

    assert first.status == "candidate-posted"
    assert second.status == "no-candidate-defensible"
    assert len(projects.projects) == 1
    assert len(deliveries.deliveries) == 1
    assert persisted_candidates == 2  # outside-market seed is not persisted
    assert all(limit == 2 for provider in (provider_a, provider_b) for _, limit in provider.calls)
    assert provider_a.calls[0][0] == "manufacturer"
    assert provider_b.calls[0][0] == "manufacturer"
    assert {cohort for cohort, _ in provider_a.calls} == {"manufacturer", "ecommerce"}
    assert {cohort for cohort, _ in provider_b.calls} == {"manufacturer", "ecommerce"}
    assert dispatched is not None and dispatched.status is DeliveryState.SENT
    assert component is not None
    assert component.expires_at == NOW + timedelta(hours=24)
    assert component.project_state is ProjectState.REVIEW

    project_dir = Path(project.artifact_dir)
    assert project_dir.parent == artifact_root
    assert {item.name for item in project_dir.iterdir()} == {
        "audit.json",
        "candidate.json",
        "dossier.vi.md",
        "evidence",
        "issues.json",
        "review-card.json",
        "score.json",
        "workflow-project.json",
    }
    assert (project_dir / "evidence" / "pages.json").is_file()
    candidate = json.loads((project_dir / "candidate.json").read_text(encoding="utf-8"))
    audit = json.loads((project_dir / "audit.json").read_text(encoding="utf-8"))
    score = json.loads((project_dir / "score.json").read_text(encoding="utf-8"))
    card = json.loads((project_dir / "review-card.json").read_text(encoding="utf-8"))
    dossier = (project_dir / "dossier.vi.md").read_text(encoding="utf-8")
    assert card["message"].count("good-manufacturer.com") == 0
    assert "**TOP OPPORTUNITIES**" in card["message"]
    assert card["message"].count("CTA-MISSING") == 1
    assert candidate["canonical_domain"] == "good-manufacturer.com"
    assert any(item["rule_id"] == "CTA-MISSING" for item in audit["findings"])
    assert score["qualified"] is True
    assert score["evidence_ids"] == ["CTA-MISSING"]
    assert score["inputs"] == {"audit": {"has_conversion_cta": False}}
    assert "browser" in audit["unavailable_inputs"]
    review_card = build_review_card(project.project_id)
    assert card == {
        "component_set": {
            "allowed_actions": [button.action for button in review_card.buttons],
            "card_type": "review",
            "component_set_id": card["component_set"]["component_set_id"],
            "expires_at": (NOW + timedelta(hours=24)).isoformat(),
            "project_id": project.project_id,
            "project_state": ProjectState.REVIEW.value,
            "state_version": 0,
        },
        "message": card["message"],
        "components": review_card.payload,
    }
    assert "channel_id" not in card["component_set"]
    assert "message_id" not in card["component_set"]
    assert "chưa xác minh" in dossier
    assert not any(ord(character) in {0xC3, 0xC4, 0xC6, 0xFFFD} for character in dossier)
    assert next(iter(deliveries.deliveries.values())).payload_path == str(
        project_dir / "review-card.json"
    )


@pytest.mark.asyncio
async def test_non_public_urls_do_not_satisfy_evidence_gate(tmp_path: Path) -> None:
    database = connect(tmp_path / "state.sqlite")
    migrate(database)
    repository = Repository(database)
    provider = _Provider(
        "provider",
        {"manufacturer": (_seed("https://private-evidence.com/", "Private Evidence"),)},
    )
    projects = _ProjectSink()
    deliveries = _DeliverySink()

    async def crawl(_url: str) -> CrawlResult:
        return CrawlResult(
            "https://private-evidence.com/",
            (
                ExtractedPage(
                    "https://private-evidence.com/",
                    "Private Evidence",
                    "Factory",
                    (),
                    (),
                    (),
                    (),
                    (),
                ),
            ),
            (),
            False,
        )

    def audit(_observation: object) -> BuiltinAuditResult:
        return BuiltinAuditResult(
            "complete",
            (
                AuditFinding(
                    "CTA-MISSING",
                    "P2",
                    ("http://127.0.0.1/private", "http://10.0.0.1/private"),
                ),
            ),
            (),
        )

    pipeline = ProductionPipeline(
        repository=repository,
        artifact_root=(tmp_path / "artifacts").resolve(),
        market=MARKET.model_copy(update={"industries": ["manufacturer"]}),
        providers=(provider,),
        crawler=crawl,
        rubric=RUBRIC,
        review_channel="review-channel",
        clock=lambda: NOW,
        project_sink=projects,
        delivery_sink=deliveries,
        audit=audit,
    )
    try:
        result = await pipeline.run()
    finally:
        repository.close()

    assert result.status == "no-candidate-defensible"
    assert not projects.projects
    assert not deliveries.deliveries


@pytest.mark.asyncio
async def test_wildcard_market_queries_every_approved_cohort_once(tmp_path: Path) -> None:
    database = connect(tmp_path / "state.sqlite")
    migrate(database)
    repository = Repository(database)
    provider = _Provider("provider", {})
    wildcard_market = MARKET.model_copy(update={"industries": ["*"]})

    async def unused_crawler(_url: str) -> CrawlResult:
        raise AssertionError("crawler must not run for an empty provider result")

    pipeline = ProductionPipeline(
        repository=repository,
        artifact_root=(tmp_path / "artifacts").resolve(),
        market=wildcard_market,
        providers=(provider,),
        crawler=unused_crawler,
        rubric=RUBRIC,
        review_channel="review-channel",
        clock=lambda: NOW,
        project_sink=_ProjectSink(),
        delivery_sink=_DeliverySink(),
    )
    try:
        result = await pipeline.run()
    finally:
        repository.close()

    assert result.status == "no-candidate-defensible"
    assert [cohort for cohort, _limit in provider.calls] == list(APPROVED_COHORTS)


@pytest.mark.asyncio
async def test_real_base_rubric_can_qualify_traceable_bad_site(tmp_path: Path) -> None:
    database = connect(tmp_path / "state.sqlite")
    migrate(database)
    repository = Repository(database)
    provider = _Provider(
        "provider",
        {"manufacturer": (_seed("https://traceable-manufacturer.com/", "Traceable"),)},
    )
    crawler, client = await _crawler_for_html(
        {
            "traceable-manufacturer.com": (
                "<html><head><title>Traceable</title>"
                "<meta name='description' content='Factory'></head>"
                "<body><h1>Factory</h1><a href='/about'>About</a></body></html>"
            )
        }
    )
    projects = _ProjectSink()
    deliveries = _DeliverySink()
    pipeline = ProductionPipeline(
        repository=repository,
        artifact_root=(tmp_path / "artifacts").resolve(),
        market=MARKET.model_copy(update={"industries": ["manufacturer"]}),
        providers=(provider,),
        crawler=crawler.crawl,
        rubric=load_rubric(Path(__file__).parents[2] / "config/scoring/base-v1.yaml"),
        review_channel="review-channel",
        clock=lambda: NOW,
        project_sink=projects,
        delivery_sink=deliveries,
        lighthouse=lambda _url: {
            "invented_metric": 999.0,
            "lcp_ms": 5000.0,
            "performance_score": 30.0,
        },
    )
    try:
        result = await pipeline.run()
    finally:
        await client.aclose()
        repository.close()

    assert result.status == "candidate-posted"
    project = next(iter(projects.projects.values()))
    score = json.loads((Path(project.artifact_dir) / "score.json").read_text(encoding="utf-8"))
    lighthouse_evidence = json.loads(
        (Path(project.artifact_dir) / "evidence" / "lighthouse.json").read_text(
            encoding="utf-8"
        )
    )
    assert score["score"] >= 75
    assert score["qualified"] is True
    assert set(score["evidence_ids"]) == {
        "LIGHTHOUSE-LCP-SLOW",
        "LIGHTHOUSE-PERFORMANCE-POOR",
        "CTA-MISSING",
        "CRAWL-CONTACT-MISSING",
    }
    assert lighthouse_evidence["status"] == "partial"
    assert lighthouse_evidence["metrics"] == {
        "lcp_ms": 5000.0,
        "performance_score": 30.0,
    }
    assert lighthouse_evidence["reason"].startswith("missing-metrics:")


@pytest.mark.asyncio
async def test_blocked_crawl_never_creates_project_or_card(tmp_path: Path) -> None:
    database = connect(tmp_path / "state.sqlite")
    migrate(database)
    repository = Repository(database)
    provider = _Provider(
        "provider",
        {"manufacturer": (_seed("https://blocked-manufacturer.com/", "Blocked"),)},
    )
    projects = _ProjectSink()
    deliveries = _DeliverySink()

    async def blocked(_url: str) -> CrawlResult:
        return CrawlResult("https://blocked-manufacturer.com/", (), (), False)

    pipeline = ProductionPipeline(
        repository=repository,
        artifact_root=(tmp_path / "artifacts").resolve(),
        market=MARKET,
        providers=(provider,),
        crawler=blocked,
        rubric=RUBRIC,
        review_channel="review-channel",
        clock=lambda: NOW,
        project_sink=projects,
        delivery_sink=deliveries,
    )
    try:
        result = await pipeline.run()
    finally:
        repository.close()

    assert result.status == "no-candidate-defensible"
    assert not projects.projects
    assert not deliveries.deliveries
    assert not list((tmp_path / "artifacts").glob("**/review-card.json"))


@pytest.mark.asyncio
async def test_provider_failures_are_partial_or_failed(tmp_path: Path) -> None:
    async def unused_crawler(_url: str) -> CrawlResult:
        raise AssertionError("crawler must not run")

    async def run_with(providers: tuple[Any, ...], suffix: str) -> str:
        database = connect(tmp_path / f"{suffix}.sqlite")
        migrate(database)
        repository = Repository(database)
        pipeline = ProductionPipeline(
            repository=repository,
            artifact_root=(tmp_path / suffix).resolve(),
            market=MARKET,
            providers=providers,
            crawler=unused_crawler,
            rubric=RUBRIC,
            review_channel="review-channel",
            clock=lambda: NOW,
            project_sink=_ProjectSink(),
            delivery_sink=_DeliverySink(),
        )
        try:
            return (await pipeline.run()).status
        finally:
            repository.close()

    assert await run_with((_FailingProvider(),), "failed") == "failed"
    assert (
        await run_with((_FailingProvider(), _Provider("empty", {})), "partial") == "partial"
    )


@pytest.mark.asyncio
async def test_production_run_caps_full_audits(tmp_path: Path) -> None:
    database = connect(tmp_path / "state.sqlite")
    migrate(database)
    repository = Repository(database)
    provider = _Provider(
        "provider",
        {
            "manufacturer": tuple(
                _seed(f"https://bounded-{index}.com/", f"Bounded {index}")
                for index in range(4)
            )
        },
    )
    calls = 0

    async def crawl(url: str) -> CrawlResult:
        nonlocal calls
        calls += 1
        return CrawlResult(url, (), (), False)

    pipeline = ProductionPipeline(
        repository=repository,
        artifact_root=(tmp_path / "artifacts").resolve(),
        market=MARKET.model_copy(update={"industries": ["manufacturer"]}),
        providers=(provider,),
        crawler=crawl,
        rubric=RUBRIC,
        review_channel="review-channel",
        clock=lambda: NOW,
        project_sink=_ProjectSink(),
        delivery_sink=_DeliverySink(),
        max_full_audits=2,
    )
    try:
        result = await pipeline.run()
    finally:
        repository.close()

    assert result.status == "no-candidate-defensible"
    assert result.evaluated_candidates == 2
    assert calls == 2


@pytest.mark.asyncio
async def test_production_persists_partial_screenshot_and_lighthouse_evidence(
    tmp_path: Path,
) -> None:
    database = connect(tmp_path / "state.sqlite")
    migrate(database)
    repository = Repository(database)
    provider = _Provider(
        "provider",
        {"manufacturer": (_seed("https://evidence-partial.com/", "Partial Evidence"),)},
    )
    crawler, client = await _crawler_for_html(
        {
            "evidence-partial.com": (
                "<html><head><title>Partial</title>"
                "<meta name='description' content='Factory'></head>"
                "<body><h1>Factory</h1><a href='/about'>About</a></body></html>"
            )
        }
    )
    projects = _ProjectSink()
    screenshot_source = tmp_path / "mobile-viewport.png"
    screenshot_source.write_bytes(b"persisted screenshot bytes")
    screenshot = ScreenshotResult(
        source_url="https://evidence-partial.com/",
        status="partial",
        attempts=2,
        screenshots=(
            ScreenshotRecord(
                viewport="mobile",
                kind="viewport",
                width=390,
                height=844,
                path=screenshot_source,
                byte_size=screenshot_source.stat().st_size,
            ),
            ScreenshotRecord(
                viewport="desktop",
                kind="full-page",
                width=1440,
                height=1200,
                path=tmp_path / "missing-desktop-full-page.png",
                byte_size=123,
            ),
        ),
        observations=(),
        console_errors=(),
        failed_requests=(),
        failures=(ScreenshotFailure(2, "desktop", "full-page", "navigation-timeout"),),
    )
    lighthouse = LighthouseRunResult(
        status="complete",
        metrics=LighthouseMetrics(
            status="complete",
            performance_score=30,
            accessibility_score=None,
            seo_score=None,
            best_practices_score=None,
            lcp_ms=5000.0,
            cls=None,
            fcp_ms=None,
            speed_index_ms=None,
            tbt_ms=None,
            unavailable_inputs=(
                "accessibility_score",
                "best_practices_score",
                "cls",
                "fcp_ms",
                "seo_score",
                "speed_index_ms",
                "tbt_ms",
            ),
        ),
    )
    pipeline = ProductionPipeline(
        repository=repository,
        artifact_root=(tmp_path / "artifacts").resolve(),
        market=MARKET.model_copy(update={"industries": ["manufacturer"]}),
        providers=(provider,),
        crawler=crawler.crawl,
        rubric=load_rubric(Path(__file__).parents[2] / "config/scoring/base-v1.yaml"),
        review_channel="review-channel",
        clock=lambda: NOW,
        project_sink=projects,
        delivery_sink=_DeliverySink(),
        screenshot=lambda _url: screenshot,
        lighthouse=lambda _url: lighthouse,
    )
    try:
        result = await pipeline.run()
    finally:
        await client.aclose()
        repository.close()

    assert result.status == "candidate-posted"
    evidence_dir = Path(next(iter(projects.projects.values())).artifact_dir) / "evidence"
    screenshots_payload = json.loads(
        (evidence_dir / "screenshots.json").read_text(encoding="utf-8")
    )
    assert screenshots_payload["status"] == "partial"
    assert screenshots_payload["reason"] == "capture-incomplete"
    assert screenshots_payload["screenshots"] == [
        {
            "byte_size": screenshot_source.stat().st_size,
            "height": 844,
            "kind": "viewport",
            "path": "evidence/screenshots/00-mobile-viewport.png",
            "viewport": "mobile",
            "width": 390,
        }
    ]
    assert (
        Path(next(iter(projects.projects.values())).artifact_dir)
        / screenshots_payload["screenshots"][0]["path"]
    ).read_bytes() == screenshot_source.read_bytes()
    lighthouse_payload = json.loads(
        (evidence_dir / "lighthouse.json").read_text(encoding="utf-8")
    )
    assert lighthouse_payload["status"] == "partial"
    assert lighthouse_payload["metrics"] == {
        "lcp_ms": 5000.0,
        "performance_score": 30,
    }
    assert lighthouse_payload["reason"].startswith("missing-metrics:")


@pytest.mark.asyncio
async def test_production_persists_unavailable_evidence_without_fabricated_metrics(
    tmp_path: Path,
) -> None:
    database = connect(tmp_path / "state.sqlite")
    migrate(database)
    repository = Repository(database)
    provider = _Provider(
        "provider",
        {"manufacturer": (_seed("https://evidence-unavailable.com/", "Unavailable"),)},
    )
    crawler, client = await _crawler_for_html(
        {
            "evidence-unavailable.com": (
                "<html><head><title>Unavailable</title>"
                "<meta name='description' content='Factory'></head>"
                "<body><h1>Factory</h1><a href='/about'>About</a></body></html>"
            )
        }
    )
    projects = _ProjectSink()

    async def screenshot_failure(_url: str) -> ScreenshotResult:
        raise RuntimeError("browser detail must not leak")

    def lighthouse_failure(_url: str) -> LighthouseRunResult:
        raise RuntimeError("lighthouse detail must not leak")

    pipeline = ProductionPipeline(
        repository=repository,
        artifact_root=(tmp_path / "artifacts").resolve(),
        market=MARKET.model_copy(update={"industries": ["manufacturer"]}),
        providers=(provider,),
        crawler=crawler.crawl,
        rubric=RUBRIC,
        review_channel="review-channel",
        clock=lambda: NOW,
        project_sink=projects,
        delivery_sink=_DeliverySink(),
        screenshot=screenshot_failure,
        lighthouse=lighthouse_failure,
    )
    try:
        result = await pipeline.run()
    finally:
        await client.aclose()
        repository.close()

    assert result.status == "candidate-posted"
    evidence_dir = Path(next(iter(projects.projects.values())).artifact_dir) / "evidence"
    assert json.loads((evidence_dir / "screenshots.json").read_text(encoding="utf-8")) == {
        "reason": "capture-failed",
        "screenshots": [],
        "status": "unavailable",
    }
    assert json.loads((evidence_dir / "lighthouse.json").read_text(encoding="utf-8")) == {
        "metrics": None,
        "reason": "collection-failed",
        "status": "unavailable",
    }
