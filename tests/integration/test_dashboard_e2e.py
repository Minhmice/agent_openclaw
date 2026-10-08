"""End-to-end coverage for the loopback dashboard and coordinator boundary."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from openclaw_web.dashboard.auth import ACTOR_IDS, TokenAuthenticator
from openclaw_web.dashboard.server import DashboardServer
from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository
from openclaw_web.lead_contracts import PortfolioEntry, RedTeamVerdict


class RecordingCoordinator:
    """Coordinator adapter double: the HTTP layer must call it, not SQLite state."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def apply(self, request: Any, actor_id: str) -> dict[str, object]:
        self.calls.append((str(request.action), str(request.target_id), actor_id))
        return {
            "status": "accepted",
            "state": "approved",
            "state_version": 1,
            "next_action": "website-brief",
            "message_vi": "Đã chuyển qua coordinator.",
        }


def _seed_database(path: Path) -> str:
    connection = connect(path)
    migrate(connection)
    repository = Repository(connection)
    run = repository.create_or_resume_run("dashboard-e2e-run", "lead-intelligence.v1")
    connection.execute(
        """
        INSERT INTO candidates (
            candidate_id, canonical_domain, normalized_name, state,
            discovered_at, updated_at, snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "candidate-e2e",
            "e2e.example",
            "e2e business",
            "review-ready",
            "2026-08-27T00:00:00Z",
            "2026-08-27T00:00:00Z",
            json.dumps({"candidate_id": "candidate-e2e", "name": "E2E Business"}),
        ),
    )
    connection.execute(
        """
        INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "project-e2e",
            "candidate-e2e",
            "review",
            0,
            json.dumps({"project_id": "project-e2e", "pages": []}),
        ),
    )
    repository.create_portfolio(
        portfolio_id="portfolio-e2e",
        run_id=run.run_id,
        status="complete",
        entries=(
            PortfolioEntry(
                entry_id="entry-e2e",
                portfolio_id="portfolio-e2e",
                candidate_id="candidate-e2e",
                rank=1,
                company_name="E2E Business",
                website_url="https://e2e.example",
                business_strength=90,
                agency_fit=80,
                digital_gap=75,
                evidence_confidence=0.95,
                red_team_verdict=RedTeamVerdict.SURVIVE,
            ),
        ),
        target=3,
        maximum=7,
    )
    connection.close()
    return run.run_id


def _request(
    server: DashboardServer,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, object] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=encoded, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    result = response.status, {key: value for key, value in response.getheaders()}, payload
    connection.close()
    return result


def test_dashboard_server_exposes_live_snapshot_and_idempotent_actions(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite"
    run_id = _seed_database(database)
    coordinator = RecordingCoordinator()
    server = DashboardServer(
        db_path=database,
        host="127.0.0.1",
        port=0,
        authenticator=TokenAuthenticator({"minh": "e2e-minh-token", "wien": "e2e-wien-token"}),
        coordinator=coordinator,
        dashboard_root=Path(__file__).parents[2] / "dashboard",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.server_address[0] == "127.0.0.1"

        static_status, static_headers, static_body = _request(server, "GET", "/")
        assert static_status == 200
        assert static_headers["Cache-Control"] == "no-store"
        assert b"Lead Intelligence" in static_body
        assert b"An Minh" in static_body

        health_status, _, health_body = _request(server, "GET", "/api/v1/health")
        assert health_status == 200
        assert json.loads(health_body)["bind"] == "127.0.0.1"

        dashboard_status, _, dashboard_body = _request(server, "GET", "/api/v1/dashboard")
        dashboard = json.loads(dashboard_body)
        assert dashboard_status == 200
        assert dashboard["schema_version"] == "dashboard.snapshot.v1"
        assert dashboard["portfolio"][0]["entry_id"] == "entry-e2e"
        assert "snapshot_json" not in dashboard_body.decode("utf-8")

        runs_status, _, runs_body = _request(server, "GET", "/api/v1/runs?limit=1")
        lead_status, _, lead_body = _request(server, "GET", "/api/v1/leads/entry-e2e")
        detail_status, _, detail_body = _request(server, "GET", f"/api/v1/runs/{run_id}")
        assert runs_status == lead_status == detail_status == 200
        assert json.loads(runs_body)[0]["run_id"] == run_id
        assert json.loads(lead_body)["project_id"] == "project-e2e"
        assert json.loads(detail_body)["run_id"] == run_id

        action = {
            "action": "lead-approve",
            "target_id": "entry-e2e",
            "expected_state_version": 0,
            "idempotency_key": "e2e-approve-1",
        }
        unauthorized, _, _ = _request(server, "POST", "/api/v1/actions", body=action)
        accepted, _, accepted_body = _request(
            server, "POST", "/api/v1/actions", token="e2e-minh-token", body=action
        )
        replayed, _, replayed_body = _request(
            server, "POST", "/api/v1/actions", token="e2e-minh-token", body=action
        )
        stale, _, stale_body = _request(
            server,
            "POST",
            "/api/v1/actions",
            token="e2e-minh-token",
            body={**action, "idempotency_key": "e2e-stale-1", "expected_state_version": 9},
        )

        assert unauthorized == 401
        assert accepted == replayed == 200
        assert accepted_body == replayed_body
        assert json.loads(accepted_body)["state"] == "approved"
        assert stale == 409
        assert json.loads(stale_body)["error"] == "stale_state"
        assert coordinator.calls == [
            ("lead-approve", "entry-e2e", ACTOR_IDS["minh"]),
        ]

        events_status, _, events_body = _request(server, "GET", "/api/v1/events?limit=10")
        assert events_status == 200
        events = json.loads(events_body)
        assert any(event["event_id"] == json.loads(accepted_body)["event_id"] for event in events)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
