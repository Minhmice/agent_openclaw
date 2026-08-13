from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import httpx
import pytest

from openclaw_web.audit.builtin import PageAuditObservation, audit_page
from openclaw_web.brief.generator import BriefPackage, BriefPipeline
from openclaw_web.crawl.service import WebsiteCrawler
from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository
from openclaw_web.delivery.components import WIEN_ID, ComponentActionService, ComponentSetRecord
from openclaw_web.delivery.openclaw_transport import SentMessage
from openclaw_web.delivery.outbox import OutboxWorker
from openclaw_web.discovery.batch import ManualUrlDiscoverySource
from openclaw_web.discovery.service import DiscoveryService
from openclaw_web.geofence.service import GeofenceService
from openclaw_web.models import Candidate, DeliveryRecord, DeliveryState
from openclaw_web.render.homepage import HomepageRenderer
from openclaw_web.review.ai import ReviewService
from openclaw_web.review.dossier import render_dossier
from openclaw_web.scoring.engine import RuleEngine
from openclaw_web.scoring.rules import load_rubric
from openclaw_web.screenshots.playwright_runner import ScreenshotRunner


class _PeerStream:
    def get_extra_info(self, name: str) -> object:
        return ("93.184.216.34", 443) if name == "server_addr" else None


class _FixtureHandler(BaseHTTPRequestHandler):
    body = """<!doctype html><html lang='vi'><head><title>Nội thất Hà Nội</title><meta name='description' content='Thiết kế nội thất văn phòng tại Hà Nội'></head><body><h1>Nội thất văn phòng</h1><p>Thiết kế và thi công nội thất.</p><a href='/contact.html'>Liên hệ báo giá</a></body></html>"""

    def do_GET(self) -> None:
        body = self.body if self.path in {"/", "/index.html"} else "<html><body><h1>Liên hệ</h1></body></html>"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _FakeModel:
    async def complete(self, _prompt: str) -> str:
        return json.dumps({
            "summary_vi": "Website có thông tin cơ bản và CTA báo giá.",
            "conversion_hypothesis": "Tăng độ rõ ràng của CTA báo giá.",
            "top_issues": [],
            "confidence_gaps": [],
            "next_action": "Duyệt review card.",
        }, ensure_ascii=False)


class _FakeTransport:
    def send(self, _delivery: DeliveryRecord) -> SentMessage:
        return SentMessage("message-e2e", "https://discord.com/channels/g/c/message-e2e")


@pytest.mark.asyncio
async def test_fixture_candidate_reaches_discord_action_after_gates(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repository = Repository(db)
    try:
        url = "https://example.com/"
        local_url = f"http://127.0.0.1:{server.server_port}/"
        source = ManualUrlDiscoverySource([{
            "url": url,
            "business_name": "Nội thất Hà Nội",
            "source_url": "https://directory.example/e2e",
            "industry_hint": "manufacturer",
            "latitude": 21.0285,
            "longitude": 105.8542,
        }])
        discovery = DiscoveryService(
            geofence=GeofenceService(21.0285, 105.8542, 80),
            repository=repository,
        )
        discovered = discovery.process(source.discover())
        assert len(discovered.candidates) == 1
        candidate = discovered.candidates[0]
        candidate_id = cast(Candidate, candidate).candidate_id

        async def handler(request: httpx.Request) -> httpx.Response:
            response = httpx.Response(
                200,
                text=_FixtureHandler.body,
                headers={"Content-Type": "text/html; charset=utf-8"},
                request=request,
            )
            response.extensions["network_stream"] = _PeerStream()
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            crawl = await WebsiteCrawler(client=client, resolver=lambda _host: ("93.184.216.34",)).crawl(url)
        finally:
            await client.aclose()
        assert crawl.pages
        page = crawl.pages[0]
        screenshots = await ScreenshotRunner(tmp_path / "screenshots", local_test_mode=True).capture(local_url)
        technical = audit_page(PageAuditObservation(page=page, requested_url=url, redirect_count=0, mobile_viewport=True, body_bytes=500, dom_nodes=20, request_count=3, request_bytes=1000, browser=screenshots))
        assert technical.status == "partial"

        rubric = RuleEngine(load_rubric(Path(__file__).parents[2] / "config/scoring/base-v1.yaml"))
        score = rubric.evaluate({
            "lighthouse": {"lcp_ms": 2500.0, "performance_score": 80.0},
            "audit": {"broken_links": 0, "has_conversion_cta": True},
            "crawl": {"has_contact_page": True},
        }, cohort_id="manufacturer")
        assert score.rubric_version == "base-v1"

        review = await ReviewService(_FakeModel()).review({
            "project_id": "project-e2e",
            "measured_metrics": {"score": score.score},
            "deterministic_rule_matches": list(score.matched_rule_ids),
            "page": {"url": page.url, "title": page.title},
        })
        dossier = render_dossier({"project_id": "project-e2e", "status": review.status, **review.data})
        assert "## Confidence gaps" in dossier

        brief_root = tmp_path / "artifacts"
        brief = await BriefPipeline(brief_root).generate({
            "project_id": "project-e2e",
            "approval": {"approved": True, "approved_by": WIEN_ID},
            "website_url": url,
            "business_name": "Nội thất Hà Nội",
        })
        package = BriefPackage.fixture("project-e2e")
        homepage = await HomepageRenderer().render_and_validate(package, brief_root / "project-e2e")
        assert brief.valid
        assert homepage.pm_handoff_path is not None
        assert not homepage.unresolved_p0_p1

        db.execute("INSERT OR IGNORE INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-e2e', ?, 'review', 0, '{}')", (candidate_id,))
        repository.enqueue_delivery(DeliveryRecord(
            delivery_id="delivery-e2e", event_type="review-card", project_id="project-e2e", channel_id="channel-e2e",
            payload_path=str(homepage.html_path), idempotency_key="review:project-e2e", status=DeliveryState.PENDING,
        ))
        sent = OutboxWorker(repository, _FakeTransport()).dispatch_once()
        assert sent is not None and sent.status is DeliveryState.SENT

        component = ComponentSetRecord("set-e2e", "channel-e2e", "message-e2e", "project-e2e", "review", ("approve",), datetime.now(UTC) + timedelta(hours=1), 0)
        calls: list[dict[str, object]] = []
        action = ComponentActionService(
            type("Store", (), {"get_component_set": lambda _self, channel_id, message_id: component})(),
            type("Coordinator", (), {"execute": lambda _self, **kwargs: calls.append(kwargs)})(),
        ).execute(channel_id="channel-e2e", message_id="message-e2e", actor_id=WIEN_ID, action="approve")
        assert action.status == "accepted"
        assert calls[0]["project_id"] == "project-e2e"
    finally:
        repository.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_unresolved_p1_blocks_pm_handoff(tmp_path: Path) -> None:
    package = BriefPackage.fixture("blocked-project")
    package.content_inventory["items"] = [
        item for item in package.content_inventory["items"] if item["type"] != "CTA"
    ]
    hero = package.page_blueprints["pages"][0]["sections"][0]
    hero["content_ids"] = [item for item in hero["content_ids"] if item != "hero-cta"]

    result = await HomepageRenderer().render_and_validate(package, tmp_path)

    assert result.unresolved_p0_p1
    assert any(issue.startswith("P1:") for issue in result.unresolved_p0_p1)
    assert result.pm_handoff_path is None
