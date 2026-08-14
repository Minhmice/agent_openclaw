import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from openclaw_web import cli

COMMANDS = (
    "audit",
    "brief",
    "calibration",
    "component-action",
    "component-callback",
    "cron-run",
    "delivery",
    "discover",
    "feedback",
    "health",
    "legacy-review",
    "validate",
)
SERVICE_COMMANDS = tuple(command for command in COMMANDS if command != "legacy-review")


class RecordingRegistry:
    def __init__(self, handler: Callable[[], object]) -> None:
        self.handler = handler
        self.dependencies: list[str] = []

    def require(self, dependency: str) -> Callable[[], object]:
        self.dependencies.append(dependency)
        return self.handler


def test_cli_registers_exactly_the_approved_command_set() -> None:
    command = get_command(cli.app)

    assert set(command.commands) == set(COMMANDS)


def test_cli_help_succeeds() -> None:
    result = CliRunner().invoke(cli.app, ["--help"])

    assert result.exit_code == 0


def test_health_json_uses_absolute_runtime_configuration() -> None:
    result = CliRunner().invoke(cli.app, ["health", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["checks"]["artifact_root_absolute"]


def test_cron_dry_run_is_offline_and_does_not_require_registry() -> None:
    result = CliRunner().invoke(cli.app, ["cron-run", "--dry-run", "--json"])

    assert result.exit_code == 0
    assert '"status":"dry-run"' in result.output


def test_cron_run_without_provider_uses_db_lock_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_db = tmp_path / "state.sqlite"
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(state_db))
    monkeypatch.setenv("OPENCLAW_WEB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)

    result = CliRunner().invoke(cli.app, ["cron-run", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "command": "cron-run",
        "exit_code": 1,
        "market": "hanoi-80km",
        "status": "failed",
    }
    assert state_db.exists()


def test_delivery_json_drains_the_real_outbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openclaw_web.runtime.drain_delivery_outbox",
        lambda: {"status": "sent", "dispatched": 1, "sent": 1, "failed": 0},
    )

    result = CliRunner().invoke(cli.app, ["delivery", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "dispatched": 1,
        "failed": 0,
        "sent": 1,
        "status": "sent",
    }


def test_legacy_review_json_calls_the_durable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "openclaw_web.runtime.run_legacy_review",
        lambda project_id: calls.append(project_id)
        or {
            "project_id": project_id,
            "status": "sent",
            "message_id": "1537000000000000000",
        },
    )

    result = CliRunner().invoke(
        cli.app, ["legacy-review", "--project-id", "vn-ntq-test", "--json"]
    )

    assert result.exit_code == 0
    assert calls == ["vn-ntq-test"]
    assert json.loads(result.output)["status"] == "sent"


def test_component_action_reads_strict_envelope_from_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "openclaw_web.runtime.run_component_action",
        lambda text: calls.append(text) or {
            "status": "accepted",
            "message_vi": "Da ghi nhan thao tac.",
            "fallback_command": "/lead-approve project-1",
        },
    )
    envelope = json.dumps(
        {
            "actor_id": "620891893659598850",
            "channel_id": "channel-1",
            "component_set_id": "set-1",
            "message_id": "message-1",
            "state_version": 2,
            "value": "project:project-1:approve",
        }
    )

    result = CliRunner().invoke(cli.app, ["component-action", "--json"], input=envelope)

    assert result.exit_code == 0
    assert calls == [envelope]
    assert json.loads(result.output)["status"] == "accepted"


def test_component_callback_reads_only_trusted_identity_from_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "openclaw_web.runtime.run_component_callback",
        lambda text: calls.append(text) or {
            "status": "accepted",
            "message_vi": "Da ghi nhan thao tac.",
            "fallback_command": "/lead-approve project-1",
        },
    )
    envelope = json.dumps(
        {
            "actor_id": "620891893659598850",
            "guild_id": "1446612692910739637",
            "message_id": "1537000000000000000",
            "value": "project:project-1:approve",
        }
    )

    result = CliRunner().invoke(cli.app, ["component-callback", "--json"], input=envelope)

    assert result.exit_code == 0
    assert calls == [envelope]
    assert json.loads(result.output)["status"] == "accepted"


def test_component_callback_rejects_oversized_stdin_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected(_text: str) -> dict[str, str]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("openclaw_web.runtime.run_component_callback", unexpected)

    result = CliRunner().invoke(
        cli.app, ["component-callback", "--json"], input="x" * 65_537
    )

    assert result.exit_code == 2
    assert not called


def test_component_action_reads_strict_envelope_from_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "openclaw_web.runtime.run_component_action",
        lambda text: calls.append(text) or {
            "status": "accepted",
            "message_vi": "Da ghi nhan thao tac.",
            "fallback_command": "/final-confirm project-1",
        },
    )
    envelope = json.dumps(
        {
            "actor_id": "620891893659598850",
            "channel_id": "channel-1",
            "component_set_id": "set-1",
            "message_id": "message-1",
            "state_version": 2,
            "value": "project:project-1:final-confirm",
        }
    )
    input_path = tmp_path / "component-action.json"
    input_path.write_text(envelope, encoding="utf-8")

    result = CliRunner().invoke(
        cli.app, ["component-action", "--input", str(input_path), "--json"]
    )

    assert result.exit_code == 0
    assert calls == [envelope]


def test_component_action_rejects_stdin_and_file_together(tmp_path: Path) -> None:
    input_path = tmp_path / "component-action.json"
    input_path.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        ["component-action", "--input", str(input_path), "--json"],
        input="{}",
    )

    assert result.exit_code == 2
    assert "stdin" in result.output.lower()


def test_component_action_rejects_oversized_file_before_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def unexpected(_text: str) -> dict[str, str]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("openclaw_web.runtime.run_component_action", unexpected)
    input_path = tmp_path / "oversized.json"
    input_path.write_bytes(b"x" * 65_537)

    result = CliRunner().invoke(
        cli.app, ["component-action", "--input", str(input_path), "--json"]
    )

    assert result.exit_code == 2
    assert not called


@pytest.mark.parametrize("command", ["discover", "audit"])
def test_local_dry_run_commands_are_offline(command: str) -> None:
    result = CliRunner().invoke(cli.app, [command, "--dry-run", "--json"])

    assert result.exit_code == 0
    assert '"status":"dry-run"' in result.output


@pytest.mark.parametrize("command", SERVICE_COMMANDS)
def test_each_default_command_reports_its_missing_dependency(command: str) -> None:
    result = CliRunner().invoke(cli.app, [command])

    assert result.exit_code == 2
    assert result.output == f"Thieu runtime dependency: {command}\n"


@pytest.mark.parametrize("command", SERVICE_COMMANDS)
def test_each_command_resolves_and_invokes_its_matching_service_once(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    handler_calls = 0

    def handler() -> None:
        nonlocal handler_calls
        handler_calls += 1

    registry = RecordingRegistry(handler)
    monkeypatch.setattr(cli, "service_registry", registry)

    result = CliRunner().invoke(cli.app, [command])

    assert result.exit_code == 0
    assert registry.dependencies == [command]
    assert handler_calls == 1


@pytest.mark.parametrize("arguments", [["--help"], *[[command] for command in COMMANDS]])
def test_cli_output_is_legacy_windows_safe(arguments: list[str]) -> None:
    result = CliRunner().invoke(cli.app, arguments)

    result.output.encode("cp1252")
