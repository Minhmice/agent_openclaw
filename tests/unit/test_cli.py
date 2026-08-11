from typer.testing import CliRunner

from openclaw_web.cli import app


def test_cli_exposes_approved_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "health",
        "audit",
        "discover",
        "brief",
        "validate",
        "feedback",
        "calibration",
        "delivery",
        "cron-run",
        "component-action",
    ):
        assert command in result.stdout


def test_cli_reports_missing_runtime_dependency() -> None:
    result = CliRunner().invoke(app, ["health"])

    assert result.exit_code == 2
    assert "health" in result.output


def test_cli_output_is_legacy_windows_safe() -> None:
    runner = CliRunner()

    for arguments in (["--help"], ["health"]):
        result = runner.invoke(app, arguments)
        result.output.encode("cp1252")
