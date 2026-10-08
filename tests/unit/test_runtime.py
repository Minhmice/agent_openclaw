from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openclaw_web.db import Repository, connect, migrate
from openclaw_web.delivery.components import (
    DEFAULT_REVIEW_REJECTION_REASON,
    ActionResult,
    ComponentActionEnvelope,
)
from openclaw_web.models import ComponentSet, ProjectState
from openclaw_web.runtime import (
    ProductionDiscoveryComposition,
    _component_result_message,
    _CoordinatorActions,
    drain_delivery_outbox,
    run_component_action,
    run_component_callback,
    run_daily_discovery,
)


class _SameVersionCoordinator:
    def action(self, **_kwargs):
        return object()

    def execute(self, _command):
        return subprocess.CompletedProcess(
            [],
            0,
            stdout='{"status":"approved","state_version":0}',
            stderr="",
        )


class _PageMutationCoordinator(_SameVersionCoordinator):
    def execute(self, _command):
        return subprocess.CompletedProcess(
            [],
            0,
            stdout='{"status":"approved","slug":"homepage"}',
            stderr="",
        )


class _ConfirmationSpy:
    confirmed = False
    confirmation: tuple[ProjectState, int] | None = None
    synchronization: tuple[ProjectState, int, int] | None = None

    def confirm_component_action(self, *_args, **_kwargs) -> None:
        self.confirmed = True
        self.confirmation = (_kwargs["state"], _kwargs["state_version"])

    def synchronize_project_state(self, *_args, **kwargs) -> bool:
        self.synchronization = (
            kwargs["state"],
            kwargs["expected_version"],
            kwargs["state_version"],
        )
        return True


def _seed_callback_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    project_id: str,
    *,
    allowed_actions: list[str],
    project_payload: dict[str, object] | None = None,
    component_state_version: int = 0,
    message_id: str = "1537000000000099000",
) -> None:
    state_db = tmp_path / "state.sqlite"
    workflow_root = tmp_path / "workflow"
    workflow_root.mkdir()
    source = Path(__file__).parents[2] / "agents/shared/workflow-coordinator.py"
    (workflow_root / "workflow-coordinator.py").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    project_dir = workflow_root / "projects" / project_id
    project_dir.mkdir(parents=True)
    payload = {
        "project_id": project_id,
        "status": "review",
        "state_version": component_state_version,
        "pages": [],
        "final_confirmations": {},
    }
    if project_payload is not None:
        payload.update(project_payload)
    (project_dir / "project.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WORKFLOW_ROOT", str(workflow_root))
    monkeypatch.setenv("OPENCLAW_WEB_DISCORD_GUILD_ID", "1446612692910739637")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    state = ProjectState(str(payload["status"]))
    version = int(payload["state_version"])
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES (?, NULL, ?, ?, '{}')",
        (project_id, state.value, version),
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id=f"set-{project_id}",
            message_id=message_id,
            channel_id="1536658476288450630",
            project_id=project_id,
            card_type="review",
            allowed_actions=allowed_actions,
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=component_state_version,
            project_state=ProjectState.REVIEW,
        )
    )
    connection.close()


def test_coordinator_confirmation_requires_strictly_newer_state_version() -> None:
    repository = _ConfirmationSpy()
    coordinator = _CoordinatorActions(_SameVersionCoordinator(), repository)

    with pytest.raises(TypeError, match="invalid project state"):
        coordinator.execute(
            project_id="project-version",
            action="approve",
            actor_id="859783610625556480",
            page_slug=None,
            reason=None,
            component_set_id="set-version",
            state_version=0,
        )

    assert not repository.confirmed


def test_component_callback_smart_approve_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "project-smart-approve"
    _seed_callback_project(
        tmp_path,
        monkeypatch,
        project_id,
        allowed_actions=["approve", "reject", "refresh"],
    )

    result = run_component_callback(
        json.dumps(
            {
                "actor_id": "859783610625556480",
                "guild_id": "1446612692910739637",
                "message_id": "1537000000000099000",
                "value": f"project:{project_id}:approve",
            }
        )
    )

    assert result["status"] == "accepted"
    assert result["message_vi"] == (
        f"Đã duyệt {project_id}. Trạng thái mới: approved. "
        "Bước tiếp theo: Website Brief."
    )


def test_component_callback_smart_reject_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "project-smart-reject"
    _seed_callback_project(
        tmp_path,
        monkeypatch,
        project_id,
        allowed_actions=["approve", "reject", "refresh"],
        message_id="1537000000000099001",
    )

    result = run_component_callback(
        json.dumps(
            {
                "actor_id": "620891893659598850",
                "guild_id": "1446612692910739637",
                "message_id": "1537000000000099001",
                "value": f"project:{project_id}:reject",
            }
        )
    )

    assert result["status"] == "accepted"
    assert result["message_vi"] == (
        f"Đã từ chối {project_id}. Lý do: {DEFAULT_REVIEW_REJECTION_REASON} "
        "Trạng thái mới: rejected."
    )


def test_component_callback_smart_refresh_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "project-smart-refresh"
    _seed_callback_project(
        tmp_path,
        monkeypatch,
        project_id,
        allowed_actions=["refresh"],
        message_id="1537000000000099002",
    )

    result = run_component_callback(
        json.dumps(
            {
                "actor_id": "620891893659598850",
                "guild_id": "1446612692910739637",
                "message_id": "1537000000000099002",
                "value": f"project:{project_id}:refresh",
            }
        )
    )

    assert result["status"] == "read-only"
    assert result["message_vi"] == (
        f"Refresh {project_id}: trạng thái review, state_version 0."
    )


def test_component_callback_smart_stale_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "project-smart-stale"
    _seed_callback_project(
        tmp_path,
        monkeypatch,
        project_id,
        allowed_actions=["approve", "refresh"],
        project_payload={"state_version": 1},
        message_id="1537000000000099003",
    )

    result = run_component_callback(
        json.dumps(
            {
                "actor_id": "620891893659598850",
                "guild_id": "1446612692910739637",
                "message_id": "1537000000000099003",
                "value": f"project:{project_id}:approve",
            }
        )
    )

    assert result["status"] == "stale"
    assert result["message_vi"] == (
        f"Thẻ của {project_id} đã cũ; hãy bấm Refresh rồi thử lại."
    )


def test_component_callback_smart_blocked_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "project-smart-blocked"
    _seed_callback_project(
        tmp_path,
        monkeypatch,
        project_id,
        allowed_actions=["approve", "refresh"],
        project_payload={
            "pages": [{"slug": "home", "unresolved_priority": "P1"}]
        },
        message_id="1537000000000099004",
    )

    result = run_component_callback(
        json.dumps(
            {
                "actor_id": "620891893659598850",
                "guild_id": "1446612692910739637",
                "message_id": "1537000000000099004",
                "value": f"project:{project_id}:approve",
            }
        )
    )

    assert result["status"] == "blocked"
    assert result["message_vi"] == (
        f"Chưa thể duyệt {project_id}: checklist hoặc P0/P1 chưa hoàn tất. "
        "Hãy xử lý gate rồi bấm Refresh."
    )


def test_component_result_message_explains_already_processed_action() -> None:
    envelope = ComponentActionEnvelope(
        actor_id="620891893659598850",
        channel_id="1536658476288450630",
        component_set_id="set-smart-duplicate",
        message_id="1537000000000099005",
        project_id="project-smart-duplicate",
        state_version=0,
        action="reject",
    )
    result = ActionResult(
        "already-processed",
        "Thao tác này đã được ghi nhận.",
        "/lead-reject project-smart-duplicate",
    )

    message = _component_result_message(
        envelope,
        result,
        {"project_id": envelope.project_id, "status": "rejected", "state_version": 1},
    )

    assert message == (
        "Thao tác từ chối cho project-smart-duplicate đã được ghi nhận trước đó. "
        "Trạng thái hiện tại: rejected."
    )


def test_page_status_does_not_create_mutation_confirmation() -> None:
    repository = _ConfirmationSpy()
    coordinator = _CoordinatorActions(_SameVersionCoordinator(), repository)

    completed = coordinator.execute(
        project_id="project-version",
        action="page-status",
        actor_id="859783610625556480",
        page_slug="homepage",
        reason=None,
        component_set_id="set-version",
        state_version=0,
    )

    assert completed.returncode == 0
    assert not repository.confirmed


def test_page_mutation_uses_canonical_project_state_for_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    workflow_root = tmp_path / "workflow"
    project_dir = workflow_root / "projects/project-page-confirmation"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        '{"project_id":"project-page-confirmation","status":"task",'
        '"state_version":3,"pages":[{"slug":"homepage","status":"approved"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_WORKFLOW_ROOT", str(workflow_root))
    repository = _ConfirmationSpy()
    coordinator = _CoordinatorActions(_PageMutationCoordinator(), repository)

    completed = coordinator.execute(
        project_id="project-page-confirmation",
        action="page-approve",
        actor_id="859783610625556480",
        page_slug="homepage",
        reason=None,
        component_set_id="set-page-confirmation",
        state_version=2,
    )

    assert completed.returncode == 0
    assert repository.confirmation == (ProjectState.TASK, 3)
    assert repository.synchronization == (ProjectState.TASK, 2, 3)


def test_runtime_uses_overpass_default_without_optional_provider_keys(
    tmp_path: Path, monkeypatch
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    market = tmp_path / "market.yaml"
    market.write_text(
        "market_id: hanoi-80km\n"
        "center:\n  name: Hanoi\n  latitude: 21.0285\n  longitude: 105.8542\n"
        "radius_km: 80\nindustries: ['*']\ntimezone: Asia/Bangkok\n",
        encoding="utf-8",
    )
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text(
        (Path(__file__).parents[2] / "config/scoring/base-v1.yaml").read_text(),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("OPENCLAW_WEB_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("OPENCLAW_WEB_MARKET_CONFIG", str(market))
    monkeypatch.setenv("OPENCLAW_WEB_SCORING_CONFIG", str(scoring))
    monkeypatch.setenv("OPENCLAW_WEB_REVIEW_CHANNEL", "review-channel")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)

    composition = ProductionDiscoveryComposition.from_environment()

    assert composition.readiness() == "ready"
    assert [provider.name for provider in composition.providers] == [
        "openstreetmap-overpass",
        "openstreetmap-nominatim",
    ]


def test_production_composition_includes_overpass_without_optional_api_keys(
    tmp_path: Path, monkeypatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    market = tmp_path / "market.yaml"
    market.write_text(
        "market_id: hanoi-80km\n"
        "center:\n  name: Hanoi\n  latitude: 21.0285\n  longitude: 105.8542\n"
        "radius_km: 80\nindustries: ['*']\ntimezone: Asia/Bangkok\n",
        encoding="utf-8",
    )
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text((Path(__file__).parents[2] / "config/scoring/base-v1.yaml").read_text(), encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WEB_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("OPENCLAW_WEB_MARKET_CONFIG", str(market))
    monkeypatch.setenv("OPENCLAW_WEB_SCORING_CONFIG", str(scoring))
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)

    composition = ProductionDiscoveryComposition.from_environment()

    assert [provider.name for provider in composition.providers] == [
        "openstreetmap-overpass",
        "openstreetmap-nominatim",
    ]
    assert composition.readiness() == "review_channel_not_configured"


def test_production_composition_requires_absolute_runtime_paths(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENCLAW_WEB_ARTIFACT_ROOT", "relative-artifacts")
    monkeypatch.setenv("OPENCLAW_WEB_MARKET_CONFIG", str(tmp_path / "market.yaml"))
    monkeypatch.setenv("OPENCLAW_WEB_SCORING_CONFIG", str(tmp_path / "scoring.yaml"))

    composition = ProductionDiscoveryComposition.from_environment()

    assert composition.readiness() == "artifact_root_not_absolute"


def test_configured_provider_fails_closed_until_full_chain_is_composed(
    tmp_path: Path, monkeypatch
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    market = tmp_path / "market.yaml"
    market.write_text(
        "market_id: hanoi-80km\n"
        "center:\n  name: Hanoi\n  latitude: 21.0285\n  longitude: 105.8542\n"
        "radius_km: 80\nindustries: ['*']\ntimezone: Asia/Bangkok\n",
        encoding="utf-8",
    )
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text((Path(__file__).parents[2] / "config/scoring/base-v1.yaml").read_text(), encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_WEB_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("OPENCLAW_WEB_MARKET_CONFIG", str(market))
    monkeypatch.setenv("OPENCLAW_WEB_SCORING_CONFIG", str(scoring))
    monkeypatch.setenv("SERPER_API_KEY", "configured")
    monkeypatch.setenv("OPENCLAW_WEB_REVIEW_CHANNEL", "review-channel")

    composition = ProductionDiscoveryComposition.from_environment()

    assert composition.readiness() == "ready"
    assert [provider.name for provider in composition.providers] == [
        "openstreetmap-overpass",
        "openstreetmap-nominatim",
        "serper",
    ]


def test_delivery_drain_reports_sent_only_after_real_outbox_state(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(tmp_path / "state.sqlite"))

    result = drain_delivery_outbox()

    assert result["status"] == "idle"
    assert result["sent"] == 0


def test_daily_discovery_uses_injected_deterministic_composition(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(tmp_path / "state.sqlite"))
    calls = 0

    def discover() -> str:
        nonlocal calls
        calls += 1
        return "candidate-posted"

    result = run_daily_discovery(discover=discover)

    assert result.status == "candidate-posted"
    assert calls == 1


def test_daily_discovery_renews_sqlite_lock_from_background_thread(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(tmp_path / "state.sqlite"))

    def discover() -> str:
        time.sleep(0.02)
        return "candidate-posted"

    result = run_daily_discovery(discover=discover, renew_interval_seconds=0.001)

    assert result.status == "candidate-posted"
    assert result.exit_code == 0


def test_failed_component_coordinator_releases_durable_claim(
    tmp_path: Path, monkeypatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    workflow_root = tmp_path / "workflow"
    workflow_root.mkdir()
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WORKFLOW_ROOT", str(workflow_root))
    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-runtime', NULL, 'review', 0, '{}')"
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="set-runtime",
            message_id="message-runtime",
            channel_id="channel-runtime",
            project_id="project-runtime",
            card_type="review",
            allowed_actions=["approve"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.REVIEW,
        )
    )
    connection.close()
    envelope = (
        '{"actor_id":"620891893659598850","channel_id":"channel-runtime",'
        '"component_set_id":"set-runtime","message_id":"message-runtime",'
        '"state_version":0,"value":"project:project-runtime:approve"}'
    )

    for _ in range(2):
        try:
            run_component_action(envelope)
        except RuntimeError:
            pass

    verification = connect(state_db)
    try:
        assert verification.execute("SELECT COUNT(*) FROM component_actions").fetchone()[0] == 0
    finally:
        verification.close()


def test_component_callback_resolves_canonical_identity_by_message_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    workflow_root = tmp_path / "workflow"
    project_dir = workflow_root / "projects/project-callback"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        '{"project_id":"project-callback","status":"review","state_version":0,'
        '"pages":[],"final_confirmations":{}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WORKFLOW_ROOT", str(workflow_root))
    monkeypatch.setenv("OPENCLAW_WEB_DISCORD_GUILD_ID", "1446612692910739637")
    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-callback', NULL, 'review', 0, '{}')"
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="set-callback",
            message_id="1537000000000000000",
            channel_id="1536658476288450630",
            project_id="project-callback",
            card_type="review",
            allowed_actions=["refresh"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.REVIEW,
        )
    )
    connection.close()

    result = run_component_callback(
        '{"actor_id":"620891893659598850",'
        '"guild_id":"1446612692910739637",'
        '"message_id":"1537000000000000000",'
        '"value":"project:project-callback:refresh"}'
    )

    assert result["status"] == "read-only"
    assert "project-callback" in result["message_vi"]
    assert result["fallback_command"] == "/refresh project-callback"


def test_component_callback_uses_approved_guild_when_gateway_env_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    workflow_root = tmp_path / "workflow"
    project_dir = workflow_root / "projects/project-default-guild"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        '{"project_id":"project-default-guild","status":"review","state_version":0,'
        '"pages":[],"final_confirmations":{}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WORKFLOW_ROOT", str(workflow_root))
    monkeypatch.delenv("OPENCLAW_WEB_DISCORD_GUILD_ID", raising=False)
    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-default-guild', NULL, 'review', 0, '{}')"
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="set-default-guild",
            message_id="1537000000000000003",
            channel_id="1536658476288450630",
            project_id="project-default-guild",
            card_type="review",
            allowed_actions=["refresh"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.REVIEW,
        )
    )
    connection.close()

    result = run_component_callback(
        '{"actor_id":"620891893659598850",'
        '"guild_id":"1446612692910739637",'
        '"message_id":"1537000000000000003",'
        '"value":"project:project-default-guild:refresh"}'
    )

    assert result["status"] == "read-only"
    assert "project-default-guild" in result["message_vi"]


def test_component_callback_rejects_forged_project_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WEB_DISCORD_GUILD_ID", "1446612692910739637")
    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-canonical', NULL, 'review', 0, '{}')"
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="set-canonical",
            message_id="1537000000000000001",
            channel_id="1536658476288450630",
            project_id="project-canonical",
            card_type="review",
            allowed_actions=["approve"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.REVIEW,
        )
    )
    connection.close()

    result = run_component_callback(
        '{"actor_id":"620891893659598850",'
        '"guild_id":"1446612692910739637",'
        '"message_id":"1537000000000000001",'
        '"value":"project:project-forged:approve"}'
    )

    assert result["status"] == "stale"
    verification = connect(state_db)
    try:
        assert verification.execute("SELECT COUNT(*) FROM component_actions").fetchone()[0] == 0
    finally:
        verification.close()


def test_component_callback_blocks_approve_when_page_has_unresolved_p1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    workflow_root = tmp_path / "workflow"
    project_dir = workflow_root / "projects/project-blocked"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        '{"project_id":"project-blocked","status":"review","state_version":0,'
        '"pages":[{"slug":"home","status":"stakeholder-review",'
        '"checklist_complete":true,"unresolved_priority":"P1"}],'
        '"final_confirmations":{}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WORKFLOW_ROOT", str(workflow_root))
    monkeypatch.setenv("OPENCLAW_WEB_DISCORD_GUILD_ID", "1446612692910739637")
    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-blocked', NULL, 'review', 0, '{}')"
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="set-blocked",
            message_id="1537000000000000002",
            channel_id="1536658476288450630",
            project_id="project-blocked",
            card_type="review",
            allowed_actions=["approve"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.REVIEW,
        )
    )
    connection.close()

    result = run_component_callback(
        '{"actor_id":"620891893659598850",'
        '"guild_id":"1446612692910739637",'
        '"message_id":"1537000000000000002",'
        '"value":"project:project-blocked:approve"}'
    )

    assert result["status"] == "blocked"


def test_runtime_rejects_page_component_without_persisted_assignment_context(
    tmp_path: Path, monkeypatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-page', NULL, 'task', 0, '{}')"
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="set-page",
            message_id="message-page",
            channel_id="channel-page",
            project_id="project-page",
            card_type="page",
            allowed_actions=["page-approve"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.TASK,
            page_id="homepage",
        )
    )
    connection.close()
    envelope = (
        '{"actor_id":"620891893659598850","channel_id":"channel-page",'
        '"component_set_id":"set-page","message_id":"message-page",'
        '"state_version":0,"value":"project:project-page:page:homepage:page-approve"}'
    )

    result = run_component_action(envelope)

    assert result["status"] in {"stale", "unauthorized"}
    assert result["fallback_command"] == "/page-approve project-page homepage"


def test_successful_component_approval_synchronizes_sqlite_state_version(
    tmp_path: Path, monkeypatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    workflow_root = tmp_path / "workflow"
    workflow_root.mkdir()
    source = Path(__file__).parents[2] / "agents/shared/workflow-coordinator.py"
    (workflow_root / "workflow-coordinator.py").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    project_dir = workflow_root / "projects/project-sync"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        '{"project_id":"project-sync","status":"review","state_version":0,'
        '"pages":[],"final_confirmations":{}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WORKFLOW_ROOT", str(workflow_root))
    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-sync', NULL, 'review', 0, '{}')"
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="set-sync",
            message_id="message-sync",
            channel_id="channel-sync",
            project_id="project-sync",
            card_type="review",
            allowed_actions=["approve"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.REVIEW,
        )
    )
    connection.close()
    envelope = (
        '{"actor_id":"859783610625556480","channel_id":"channel-sync",'
        '"component_set_id":"set-sync","message_id":"message-sync",'
        '"state_version":0,"value":"project:project-sync:approve"}'
    )

    result = run_component_action(envelope)

    assert result["status"] == "accepted"
    verification = connect(state_db)
    try:
        row = verification.execute(
            "SELECT state, state_version FROM projects WHERE project_id = 'project-sync'"
        ).fetchone()
        assert (row["state"], row["state_version"]) == ("approved", 1)
    finally:
        verification.close()


def test_confirmed_coordinator_action_reconciles_after_sqlite_cas_failure_on_retry(
    tmp_path: Path, monkeypatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    workflow_root = tmp_path / "workflow"
    workflow_root.mkdir()
    source = Path(__file__).parents[2] / "agents/shared/workflow-coordinator.py"
    (workflow_root / "workflow-coordinator.py").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    project_dir = workflow_root / "projects/project-reconcile"
    project_dir.mkdir(parents=True)
    project_file = project_dir / "project.json"
    project_file.write_text(
        '{"project_id":"project-reconcile","status":"review","state_version":0,'
        '"pages":[],"final_confirmations":{}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WORKFLOW_ROOT", str(workflow_root))
    connection = connect(state_db)
    migrate(connection)
    repository = Repository(connection)
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-reconcile', NULL, 'review', 0, '{}')"
    )
    repository.insert_component_set(
        ComponentSet(
            component_set_id="set-reconcile",
            message_id="message-reconcile",
            channel_id="channel-reconcile",
            project_id="project-reconcile",
            card_type="review",
            allowed_actions=["approve"],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            state_version=0,
            project_state=ProjectState.REVIEW,
        )
    )
    connection.close()
    envelope = (
        '{"actor_id":"859783610625556480","channel_id":"channel-reconcile",'
        '"component_set_id":"set-reconcile","message_id":"message-reconcile",'
        '"state_version":0,"value":"project:project-reconcile:approve"}'
    )
    synchronize = Repository.synchronize_project_state
    synchronization_calls = 0

    def fail_first_synchronization(self: Repository, *args, **kwargs) -> bool:
        nonlocal synchronization_calls
        synchronization_calls += 1
        if synchronization_calls == 1:
            return False
        return synchronize(self, *args, **kwargs)

    monkeypatch.setattr(Repository, "synchronize_project_state", fail_first_synchronization)

    with pytest.raises(RuntimeError, match="synchronization"):
        run_component_action(envelope)

    coordinator_state = json.loads(project_file.read_text(encoding="utf-8"))
    assert (coordinator_state["status"], coordinator_state["state_version"]) == (
        "approved",
        1,
    )
    after_failure = connect(state_db)
    try:
        action = after_failure.execute(
            "SELECT confirmed_state, confirmed_state_version FROM component_actions"
        ).fetchone()
        project = after_failure.execute(
            "SELECT state, state_version FROM projects WHERE project_id = 'project-reconcile'"
        ).fetchone()
        assert (action["confirmed_state"], action["confirmed_state_version"]) == (
            "approved",
            1,
        )
        assert (project["state"], project["state_version"]) == ("review", 0)
    finally:
        after_failure.close()

    retry = run_component_action(envelope)

    assert retry["status"] == "already-processed"
    assert synchronization_calls == 1
    verification = connect(state_db)
    try:
        project = verification.execute(
            "SELECT state, state_version FROM projects WHERE project_id = 'project-reconcile'"
        ).fetchone()
        assert (project["state"], project["state_version"]) == ("approved", 1)
    finally:
        verification.close()
    coordinator_state = json.loads(project_file.read_text(encoding="utf-8"))
    assert coordinator_state["state_version"] == 1
