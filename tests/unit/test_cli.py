from collections.abc import Callable

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from openclaw_web import cli

COMMANDS = (
    "audit",
    "brief",
    "calibration",
    "component-action",
    "cron-run",
    "delivery",
    "discover",
    "feedback",
    "health",
    "validate",
)


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


@pytest.mark.parametrize("command", COMMANDS)
def test_each_default_command_reports_its_missing_dependency(command: str) -> None:
    result = CliRunner().invoke(cli.app, [command])

    assert result.exit_code == 2
    assert result.output == f"Thieu runtime dependency: {command}\n"


@pytest.mark.parametrize("command", COMMANDS)
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
