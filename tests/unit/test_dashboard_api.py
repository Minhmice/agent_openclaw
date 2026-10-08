import json
import sqlite3
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from openclaw_web.dashboard.auth import ACTOR_IDS, TokenAuthenticator
from openclaw_web.dashboard.models import DashboardAction
from openclaw_web.dashboard.read_repository import DashboardDataUnavailable, DashboardReadRepository
from openclaw_web.dashboard.server import MAX_BODY_BYTES, DashboardServer
from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository
from openclaw_web.lead_contracts import PortfolioEntry, RedTeamVerdict


def _seed_db(
    path: Path,
    *,
    asset_root: Path | None = None,
    screenshot_asset: str | None = None,
    public_contact: str | None = None,
) -> None:
    connection = connect(path)
    migrate(connection)
    repository = Repository(connection)
    run = repository.create_or_resume_run("dashboard-run", "lead-intelligence.v1")
    connection.execute(
        """
        INSERT INTO candidates (
            candidate_id, canonical_domain, normalized_name, normalized_address,
            state, discovered_at, updated_at, snapshot_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "candidate-dashboard",
            "example.com",
            "dashboard co",
            None,
            "review-ready",
            "2026-08-27T00:00:00Z",
            "2026-08-27T00:00:00Z",
            json.dumps({"candidate_id": "candidate-dashboard", "name": "Dashboard Co"}),
        ),
    )
    connection.execute(
        """
        INSERT INTO projects (
            project_id, candidate_id, state, state_version, snapshot_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "project-dashboard",
            "candidate-dashboard",
            "review",
            0,
            json.dumps(
                {
                    "project_id": "project-dashboard",
                    "pages": [
                        {
                            "slug": "homepage",
                            "status": "in-progress",
                            "owner_id": ACTOR_IDS["wien"],
                            "checklist_complete": False,
                            "next_action": "page-done",
                        },
                        {
                            "slug": "pricing",
                            "status": "planned",
                            "owner": "Minh",
                            "checklist_complete": True,
                            "next_action": "page-approve",
                        },
                    ],
                }
            ),
        ),
    )
    entry = PortfolioEntry(
        entry_id="entry-dashboard",
        portfolio_id="portfolio-dashboard",
        candidate_id="candidate-dashboard",
        rank=1,
        company_name="Dashboard Co",
        website_url="https://example.com",
        business_strength=80,
        agency_fit=80,
        digital_gap=80,
        evidence_confidence=0.9,
        red_team_verdict=RedTeamVerdict.SURVIVE,
        state_version=0,
        screenshot_asset=screenshot_asset,
        public_contact=public_contact,
    )
    repository.create_portfolio(
        portfolio_id="portfolio-dashboard",
        run_id=run.run_id,
        status="complete",
        entries=(entry,),
    )
    if asset_root is not None and screenshot_asset is not None:
        candidate_dir = asset_root / "candidate-dashboard"
        candidate_dir.mkdir(parents=True)
        (candidate_dir / screenshot_asset).write_bytes(b"png-data")
    connection.close()


class FakeCoordinator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def apply(self, request: object, actor_id: str) -> dict[str, object]:
        action = str(request.action)  # type: ignore[attr-defined]
        target_id = str(request.target_id)  # type: ignore[attr-defined]
        self.calls.append((action, target_id, actor_id))
        return {
            "status": "accepted",
            "state": "approved" if action == "lead-approve" else "watching",
            "state_version": 1,
            "next_action": "website-brief",
            "message_vi": "Da tiep nhan",
        }


class FlakyCoordinator(FakeCoordinator):
    def apply(self, request: object, actor_id: str) -> dict[str, object]:
        if not self.calls:
            action = str(request.action)  # type: ignore[attr-defined]
            target_id = str(request.target_id)  # type: ignore[attr-defined]
            self.calls.append((action, target_id, actor_id))
            raise RuntimeError("provider unavailable")
        return super().apply(request, actor_id)


def _server(
    tmp_path: Path,
    coordinator: FakeCoordinator | None = None,
    *,
    asset_root: Path | None = None,
    screenshot_asset: str | None = None,
    public_contact: str | None = None,
) -> DashboardServer:
    db_path = tmp_path / "state.sqlite"
    _seed_db(
        db_path,
        asset_root=asset_root,
        screenshot_asset=screenshot_asset,
        public_contact=public_contact,
    )
    return DashboardServer(
        db_path=db_path,
        host="127.0.0.1",
        port=0,
        authenticator=TokenAuthenticator({"minh": "minh-token", "wien": "wien-token"}),
        coordinator=coordinator,
        dashboard_root=Path("dashboard"),
        asset_root=asset_root,
    )


@pytest.fixture
def running_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    coordinator = FakeCoordinator()
    server = _server(tmp_path, coordinator)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, coordinator
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _request(
    server: DashboardServer,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, object] | None = None,
    raw_body: bytes | None = None,
):  # type: ignore[no-untyped-def]
    if body is not None and raw_body is not None:
        raise ValueError("body and raw_body are mutually exclusive")
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        encoded = raw_body
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=encoded, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, response.getheaders(), raw


def test_authenticator_derives_actor_without_exposing_tokens() -> None:
    auth = TokenAuthenticator({"minh": "minh-token", "wien": "wien-token"})

    assert auth.authenticate("Bearer minh-token") == "minh"
    assert auth.actor_id("minh") == ACTOR_IDS["minh"]
    assert auth.authenticate("Bearer wrong") is None
    assert "minh-token" not in repr(auth)


def test_both_canonical_actors_can_submit_final_confirmation() -> None:
    auth = TokenAuthenticator({"minh": "minh-token", "wien": "wien-token"})

    assert auth.allows("minh", DashboardAction.FINAL_CONFIRM)
    assert auth.allows("wien", DashboardAction.FINAL_CONFIRM)


def test_dashboard_get_is_sanitized_and_uncached(running_server) -> None:  # type: ignore[no-untyped-def]
    server, _ = running_server

    status, headers, raw = _request(server, "GET", "/api/v1/dashboard")

    assert status == 200
    assert dict(headers)["Cache-Control"] == "no-store"
    payload = json.loads(raw)
    assert payload["portfolio"][0]["company_name"] == "Dashboard Co"
    assert payload["portfolio"][0]["project_id"] == "project-dashboard"
    assert [page["page_slug"] for page in payload["portfolio"][0]["pages"]] == [
        "homepage",
        "pricing",
    ]
    assert "snapshot_json" not in raw.decode("utf-8")


def test_dashboard_action_requires_token_and_uses_coordinator(running_server) -> None:  # type: ignore[no-untyped-def]
    server, coordinator = running_server
    body = {
        "action": "lead-approve",
        "target_id": "entry-dashboard",
        "expected_state_version": 0,
        "idempotency_key": "dashboard-action-1",
    }

    unauthorized, _, _ = _request(server, "POST", "/api/v1/actions", body=body)
    forbidden, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="wien-token",
        body={
            **body,
            "action": "lead-reject",
            "reason": "not a fit",
            "idempotency_key": "dashboard-action-2",
        },
    )
    accepted, _, raw = _request(server, "POST", "/api/v1/actions", token="minh-token", body=body)

    assert unauthorized == 401
    assert forbidden == 403
    assert accepted == 200
    assert json.loads(raw)["state"] == "approved"
    assert coordinator.calls == [("lead-approve", "entry-dashboard", ACTOR_IDS["minh"])]


def test_dashboard_action_records_sanitized_feedback_after_coordinator_success(
    running_server,
) -> None:  # type: ignore[no-untyped-def]
    server, _ = running_server
    status, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="minh-token",
        body={
            "action": "lead-approve",
            "target_id": "entry-dashboard",
            "expected_state_version": 0,
            "idempotency_key": "dashboard-feedback",
        },
    )

    connection = sqlite3.connect(server.db_path)
    connection.row_factory = sqlite3.Row
    try:
        feedback = connection.execute(
            "SELECT project_id, actor_id, snapshot_json FROM feedback"
        ).fetchone()
    finally:
        connection.close()

    assert status == 200
    assert feedback is not None
    feedback_snapshot = json.loads(feedback["snapshot_json"])
    assert (feedback["project_id"], feedback["actor_id"], feedback_snapshot["action"]) == (
        "project-dashboard",
        ACTOR_IDS["minh"],
        "lead-approve",
    )
    assert "dashboard-feedback" in feedback["snapshot_json"]


def test_dashboard_action_replay_is_idempotent(running_server) -> None:  # type: ignore[no-untyped-def]
    server, coordinator = running_server
    body = {
        "action": "watch-lead",
        "target_id": "entry-dashboard",
        "expected_state_version": 0,
        "idempotency_key": "dashboard-replay",
    }

    first, _, raw_first = _request(server, "POST", "/api/v1/actions", token="minh-token", body=body)
    second, _, raw_second = _request(server, "POST", "/api/v1/actions", token="minh-token", body=body)

    assert first == second == 200
    assert json.loads(raw_first) == json.loads(raw_second)
    assert len(coordinator.calls) == 1


def test_dashboard_page_action_uses_project_version_and_canonical_actor(
    running_server,
) -> None:  # type: ignore[no-untyped-def]
    server, coordinator = running_server
    body = {
        "action": "page-status",
        "target_id": "project-dashboard",
        "page_slug": "homepage",
        "expected_state_version": 0,
        "idempotency_key": "dashboard-page-status",
    }

    status, _, raw = _request(server, "POST", "/api/v1/actions", token="wien-token", body=body)

    assert status == 200
    assert json.loads(raw)["status"] == "accepted"
    assert coordinator.calls == [("page-status", "project-dashboard", ACTOR_IDS["wien"])]


def test_dashboard_lead_action_uses_mapped_project_version_for_cas(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite"
    _seed_db(database)
    connection = connect(database)
    connection.execute(
        "UPDATE projects SET state_version = 2 WHERE project_id = 'project-dashboard'"
    )
    connection.commit()
    connection.close()

    class CoordinatorAtNextProjectVersion(FakeCoordinator):
        def apply(self, request: object, actor_id: str) -> dict[str, object]:
            action = str(request.action)  # type: ignore[attr-defined]
            target_id = str(request.target_id)  # type: ignore[attr-defined]
            self.calls.append((action, target_id, actor_id))
            return {
                "status": "accepted",
                "state": "approved",
                "state_version": 3,
                "next_action": "website-brief",
                "message_vi": "Da tiep nhan",
            }

    coordinator = CoordinatorAtNextProjectVersion()
    server = DashboardServer(
        db_path=database,
        host="127.0.0.1",
        port=0,
        authenticator=TokenAuthenticator({"minh": "minh-token"}),
        coordinator=coordinator,
        dashboard_root=Path("dashboard"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        lead_status, _, lead_raw = _request(server, "GET", "/api/v1/leads/entry-dashboard")
        action_status, _, action_raw = _request(
            server,
            "POST",
            "/api/v1/actions",
            token="minh-token",
            body={
                "action": "lead-approve",
                "target_id": "entry-dashboard",
                "expected_state_version": 2,
                "idempotency_key": "project-version-cas",
            },
        )
        stale_status, _, _ = _request(
            server,
            "POST",
            "/api/v1/actions",
            token="minh-token",
            body={
                "action": "lead-approve",
                "target_id": "entry-dashboard",
                "expected_state_version": 0,
                "idempotency_key": "project-version-stale",
            },
        )
        assert lead_status == 200
        assert json.loads(lead_raw)["project_state_version"] == 2
        assert action_status == 200
        assert json.loads(action_raw)["state_version"] == 3
        assert stale_status == 409
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_dashboard_lead_state_filter_uses_effective_mapped_project_state(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite"
    _seed_db(database)
    connection = connect(database)
    connection.execute(
        "UPDATE projects SET snapshot_json = ? WHERE project_id = 'project-dashboard'",
        (json.dumps({"project_id": "project-dashboard", "lead_state": "selected", "pages": []}),),
    )
    connection.commit()
    connection.close()

    read_repository = DashboardReadRepository(database)

    selected = read_repository.list_leads(state="selected")
    awaiting = read_repository.list_leads(state="awaiting-command")

    assert [lead.entry_id for lead in selected] == ["entry-dashboard"]
    assert awaiting == ()


def test_dashboard_rejects_a_coordinator_version_that_does_not_advance(
    running_server,
) -> None:  # type: ignore[no-untyped-def]
    server, coordinator = running_server

    original_apply = coordinator.apply

    def invalid_version_apply(request: object, actor_id: str) -> dict[str, object]:
        result = original_apply(request, actor_id)
        result["state_version"] = 0
        return result

    coordinator.apply = invalid_version_apply  # type: ignore[method-assign]
    status, _, raw = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="minh-token",
        body={
            "action": "lead-approve",
            "target_id": "entry-dashboard",
            "expected_state_version": 0,
            "idempotency_key": "coordinator-version-regression",
        },
    )

    assert status == 503
    assert json.loads(raw)["error"] == "coordinator_invalid"


def test_failed_coordinator_receipt_can_resume_with_same_idempotency_key(tmp_path: Path) -> None:
    coordinator = FlakyCoordinator()
    server = _server(tmp_path, coordinator)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = {
        "action": "watch-lead",
        "target_id": "entry-dashboard",
        "expected_state_version": 0,
        "idempotency_key": "dashboard-transient-failure",
    }
    try:
        first, _, first_raw = _request(
            server, "POST", "/api/v1/actions", token="minh-token", body=body
        )
        second, _, second_raw = _request(
            server, "POST", "/api/v1/actions", token="minh-token", body=body
        )
        assert first == 503
        assert "provider unavailable" not in first_raw.decode("utf-8")
        assert second == 200
        assert json.loads(second_raw)["state"] == "watching"
        assert len(coordinator.calls) == 2
        assert coordinator.calls[0][2] == ACTOR_IDS["minh"]
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_stale_action_replay_returns_the_same_sanitized_conflict(running_server) -> None:  # type: ignore[no-untyped-def]
    server, coordinator = running_server
    body = {
        "action": "lead-approve",
        "target_id": "entry-dashboard",
        "expected_state_version": 3,
        "idempotency_key": "dashboard-stale-replay",
    }

    first, _, first_raw = _request(server, "POST", "/api/v1/actions", token="minh-token", body=body)
    second, _, second_raw = _request(server, "POST", "/api/v1/actions", token="minh-token", body=body)

    assert first == second == 409
    assert first_raw == second_raw
    assert json.loads(first_raw)["error"] == "stale_state"
    assert coordinator.calls == []


def test_dashboard_action_rejects_stale_state_before_coordinator(running_server) -> None:  # type: ignore[no-untyped-def]
    server, coordinator = running_server
    body = {
        "action": "lead-approve",
        "target_id": "entry-dashboard",
        "expected_state_version": 3,
        "idempotency_key": "dashboard-stale",
    }

    status, _, raw = _request(server, "POST", "/api/v1/actions", token="minh-token", body=body)

    assert status == 409
    assert json.loads(raw)["error"] == "stale_state"
    assert coordinator.calls == []


def test_dashboard_action_validates_actor_free_payload_and_required_context(running_server) -> None:  # type: ignore[no-untyped-def]
    server, coordinator = running_server
    base = {
        "target_id": "entry-dashboard",
        "expected_state_version": 0,
    }

    page_without_slug, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="minh-token",
        body={**base, "action": "page-status", "idempotency_key": "missing-page-slug"},
    )
    block_without_slug, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="minh-token",
        body={
            **base,
            "action": "block",
            "reason": "blocked for test",
            "idempotency_key": "missing-block-page-slug",
        },
    )
    reject_without_reason, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="minh-token",
        body={**base, "action": "lead-reject", "idempotency_key": "missing-reason"},
    )
    forged_actor, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="minh-token",
        body={
            **base,
            "action": "watch-lead",
            "idempotency_key": "forged-actor",
            "actor_id": "wien",
        },
    )
    bool_version, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="minh-token",
        body={
            **base,
            "action": "watch-lead",
            "idempotency_key": "bool-state-version",
            "expected_state_version": True,
        },
    )

    assert (
        page_without_slug
        == block_without_slug
        == reject_without_reason
        == forged_actor
        == bool_version
        == 400
    )
    assert coordinator.calls == []


def test_dashboard_exposes_only_existing_allowlisted_screenshot_and_public_contact(tmp_path: Path) -> None:
    asset_root = tmp_path / "artifacts"
    server = _server(
        tmp_path,
        FakeCoordinator(),
        asset_root=asset_root,
        screenshot_asset="screenshot.png",
        public_contact="hello@example.com",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, raw = _request(server, "GET", "/api/v1/dashboard")
        payload = json.loads(raw)
        lead = payload["portfolio"][0]
        assert status == 200
        assert lead["public_contact"] == "hello@example.com"
        assert lead["screenshot_url"] == "/api/v1/assets/candidate-dashboard/screenshot.png"

        asset_status, _, asset = _request(
            server,
            "GET",
            "/api/v1/assets/candidate-dashboard/screenshot.png",
        )
        missing_status, _, _ = _request(
            server,
            "GET",
            "/api/v1/assets/candidate-dashboard/../../state.sqlite",
        )
        assert asset_status == 200
        assert asset == b"png-data"
        assert missing_status == 404
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_dashboard_read_model_is_query_only_and_missing_database_does_not_get_created(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.sqlite"
    server = DashboardServer(
        db_path=missing,
        port=0,
        authenticator=TokenAuthenticator({"minh": "minh-token"}),
        dashboard_root=Path("dashboard"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, _ = _request(server, "GET", "/api/v1/health")
        unavailable, _, _ = _request(server, "GET", "/api/v1/dashboard")
        assert status == 200
        assert unavailable == 503
        assert not missing.exists()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_dashboard_bounds_json_body_and_rejects_invalid_methods(running_server) -> None:  # type: ignore[no-untyped-def]
    server, _ = running_server

    oversized, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="minh-token",
        raw_body=b"x" * (MAX_BODY_BYTES + 1),
    )
    invalid_json, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        token="minh-token",
        raw_body=b"{not-json",
    )
    unauthorized_oversized, _, _ = _request(
        server,
        "POST",
        "/api/v1/actions",
        raw_body=b"x" * (MAX_BODY_BYTES + 1),
    )
    health_post, _, _ = _request(server, "POST", "/api/v1/health")

    assert oversized == 413
    assert invalid_json == 400
    assert unauthorized_oversized == 401
    assert health_post == 405


def test_dashboard_read_repository_is_query_only(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    _seed_db(db_path)
    read_repository = DashboardReadRepository(db_path)

    with pytest.raises(DashboardDataUnavailable), read_repository.connection() as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        connection.execute("CREATE TABLE should_not_be_created (value TEXT)")

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'should_not_be_created'"
        ).fetchone() is None
    finally:
        connection.close()


def test_dashboard_does_not_migrate_an_old_database_without_v4(tmp_path: Path) -> None:
    db_path = tmp_path / "old-state.sqlite"
    connection = connect(db_path)
    migrate(connection)
    for table in (
        "dashboard_action_receipts",
        "portfolio_deliveries",
        "portfolio_entries",
        "portfolios",
    ):
        connection.execute(f"DROP TABLE {table}")
    connection.execute("DELETE FROM schema_migrations WHERE version = 4")
    connection.commit()
    connection.close()

    server = DashboardServer(
        db_path=db_path,
        port=0,
        authenticator=TokenAuthenticator({"minh": "minh-token"}),
        coordinator=FakeCoordinator(),
        dashboard_root=Path("dashboard"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, _ = _request(server, "GET", "/api/v1/dashboard")
        assert status == 503
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'portfolios'"
        ).fetchone() is None
    finally:
        connection.close()


def test_dashboard_redacts_raw_stage_errors_and_supports_head_without_cors(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    _seed_db(db_path)
    connection = connect(db_path)
    row = connection.execute("SELECT run_id, snapshot_json FROM runs LIMIT 1").fetchone()
    assert row is not None
    snapshot = json.loads(row["snapshot_json"])
    snapshot["stages"] = [
        {
            "stage_name": "deep_audit",
            "status": "failed",
            "attempt_count": 1,
            "error": "API_KEY=do-not-leak",
        }
    ]
    snapshot["metadata"] = {
        "PRIVATE_KEY": "private-key-must-not-leak",
        "lead_stage_outcomes": [{"input_refs": {"token": "secret-must-not-leak"}}],
    }
    connection.execute(
        "UPDATE runs SET snapshot_json = ? WHERE run_id = ?",
        (json.dumps(snapshot), row["run_id"]),
    )
    connection.commit()
    connection.close()

    server = DashboardServer(
        db_path=db_path,
        port=0,
        authenticator=TokenAuthenticator({"minh": "minh-token"}),
        coordinator=FakeCoordinator(),
        dashboard_root=Path("dashboard"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, raw = _request(server, "GET", "/api/v1/dashboard")
        head_status, _, head_raw = _request(server, "HEAD", "/api/v1/dashboard")
        assert status == head_status == 200
        assert "do-not-leak" not in raw.decode("utf-8")
        assert "must-not-leak" not in raw.decode("utf-8")
        assert dict(headers).get("Access-Control-Allow-Origin") is None
        assert head_raw == b""
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_dashboard_rejects_symlinked_asset(tmp_path: Path) -> None:
    asset_root = tmp_path / "artifacts"
    server = _server(
        tmp_path,
        FakeCoordinator(),
        asset_root=asset_root,
        screenshot_asset="screenshot.png",
    )
    candidate_dir = asset_root / "candidate-dashboard"
    linked_target = tmp_path / "outside.png"
    linked_target.write_bytes(b"outside")
    asset = candidate_dir / "screenshot.png"
    asset.unlink()
    try:
        asset.symlink_to(linked_target)
    except OSError:
        server.server_close()
        pytest.skip("symlink creation is unavailable on this Windows host")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, raw = _request(server, "GET", "/api/v1/dashboard")
        asset_status, _, _ = _request(
            server,
            "GET",
            "/api/v1/assets/candidate-dashboard/screenshot.png",
        )
        assert status == 200
        assert json.loads(raw)["portfolio"][0]["screenshot_url"] is None
        assert asset_status == 404
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
