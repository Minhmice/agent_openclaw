import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openclaw_web.db import connect, migrate
from openclaw_web.health import (
    HealthService,
    HealthSettings,
    ProbeResult,
    _playwright_ready,
    _subprocess_probe,
)
from openclaw_web.observability import apply_retention, redact


def test_health_reports_missing_discovery_provider_without_breaking_manual_audit(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    state_db = tmp_path / "state.sqlite"
    connection = connect(state_db)
    migrate(connection)
    connection.close()
    market = tmp_path / "market.yaml"
    market.write_text("market_id: hanoi-80km", encoding="utf-8")
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text("version: test", encoding="utf-8")
    schemas = tmp_path / "schemas"
    schemas.mkdir()

    report = HealthService(
        HealthSettings(
            artifact_root=artifact_root,
            state_db=state_db,
            market_config=market,
            scoring_config=scoring,
            schema_root=schemas,
            discovery_providers=(),
        ),
        executable=lambda name: f"/usr/bin/{name}",
        browser_ready=lambda: True,
        probe=lambda command, timeout: ProbeResult(command[0], True),
    ).check()

    assert report.manual_audit_ready
    assert not report.discovery_ready


def test_health_rejects_relative_artifact_root_without_creating_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    report = HealthService(HealthSettings(artifact_root=Path(".openclaw-web-artifacts"))).check()

    assert not report.checks["artifact_root_absolute"]
    assert not (tmp_path / ".openclaw-web-artifacts").exists()


def test_health_external_probes_are_bounded_and_redacted(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], float]] = []

    def probe(command: tuple[str, ...], timeout: float) -> ProbeResult:
        calls.append((command, timeout))
        return ProbeResult(command[0], command[-1] != "--probe", detail="must-not-escape")

    report = HealthService(
        HealthSettings(artifact_root=tmp_path.resolve(), external_probe_timeout_seconds=2.5),
        executable=lambda _name: "/usr/bin/tool",
        browser_ready=lambda: False,
        probe=probe,
    ).check()

    assert calls
    assert all(timeout == 2.5 for _, timeout in calls)
    assert all(isinstance(value, bool) for value in report.checks.values())
    assert "must-not-escape" not in repr(report)


def test_health_uses_a_longer_but_bounded_default_for_slow_discord_probe(
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[str, ...], float]] = []

    def probe(command: tuple[str, ...], timeout: float) -> ProbeResult:
        calls.append((command, timeout))
        return ProbeResult(command[0], True)

    report = HealthService(
        HealthSettings(
            artifact_root=tmp_path.resolve(),
            discovery_providers=("openstreetmap-overpass",),
        ),
        executable=lambda _name: "/usr/bin/tool",
        browser_ready=lambda: True,
        probe=probe,
    ).check()

    assert report.checks["discord"]
    assert calls
    assert all(timeout == 15.0 for _, timeout in calls)


def test_health_probe_timeout_fails_closed_without_command_output() -> None:
    result = _subprocess_probe(
        (sys.executable, "-c", "import time; time.sleep(0.1)"),
        0.001,
    )

    assert not result.ready
    assert result.detail == "timeout"


def test_browser_readiness_uses_configured_executable_without_starting_driver(
    tmp_path: Path, monkeypatch
) -> None:
    chrome = tmp_path / "chrome"
    chrome.write_bytes(b"browser")
    monkeypatch.setenv("CHROME_PATH", str(chrome))
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    assert _playwright_ready()


def test_health_distinguishes_disabled_timer_from_unhealthy_timer(tmp_path: Path) -> None:
    def probe(command: tuple[str, ...], _timeout: float) -> ProbeResult:
        if "is-enabled" in command:
            return ProbeResult("systemctl", False, detail="disabled")
        if "is-active" in command:
            return ProbeResult("systemctl", False, detail="inactive")
        return ProbeResult(command[0], True)

    report = HealthService(
        HealthSettings(
            artifact_root=tmp_path.resolve(),
            discovery_providers=("openstreetmap-overpass",),
        ),
        executable=lambda _name: "/usr/bin/tool",
        browser_ready=lambda: True,
        probe=probe,
    ).check()

    assert not report.checks["timer_enabled"]
    assert not report.checks["timer_active"]
    assert "timer_disabled" in report.discovery_blockers
    assert "timer_unhealthy" not in report.discovery_blockers


def test_health_distinguishes_missing_first_run(tmp_path: Path) -> None:
    state_db = tmp_path / "state.sqlite"
    connection = connect(state_db)
    migrate(connection)
    connection.close()

    def probe(command: tuple[str, ...], _timeout: float) -> ProbeResult:
        return ProbeResult(command[0], True)

    report = HealthService(
        HealthSettings(
            artifact_root=tmp_path.resolve(),
            state_db=state_db,
            discovery_providers=("openstreetmap-overpass",),
        ),
        executable=lambda _name: "/usr/bin/tool",
        browser_ready=lambda: True,
        probe=probe,
    ).check()

    assert not report.checks["last_run"]
    assert not report.checks["last_run_present"]
    assert "last_run_missing" in report.discovery_blockers


def test_health_reports_database_migrations_last_run_and_outbox(tmp_path: Path) -> None:
    state_db = tmp_path / "state.sqlite"
    connection = connect(state_db)
    migrate(connection)
    connection.execute(
        "INSERT INTO runs (run_id,idempotency_key,config_version,status,started_at,snapshot_json) "
        "VALUES ('r1','k1','v1','completed','2026-08-13T00:00:00Z','{}')"
    )
    connection.commit()
    connection.close()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()

    report = HealthService(
        HealthSettings(artifact_root=artifact_root, state_db=state_db),
        executable=lambda _name: None,
        browser_ready=lambda: False,
    ).check()

    assert report.checks["database"]
    assert report.checks["migrations"]
    assert report.checks["last_run"]
    assert report.checks["outbox"]


def test_health_settings_from_environment_uses_only_absolute_paths(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENCLAW_WEB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("OPENCLAW_WEB_STATE_DB", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("OPENCLAW_WEB_MARKET_CONFIG", str(tmp_path / "market.yaml"))
    monkeypatch.setenv("OPENCLAW_WEB_SCORING_CONFIG", str(tmp_path / "scoring.yaml"))
    monkeypatch.setenv("OPENCLAW_WEB_SCHEMA_ROOT", str(tmp_path / "schemas"))
    monkeypatch.setenv("SERPER_API_KEY", "never-return-this-value")

    settings = HealthSettings.from_environment()

    assert settings.artifact_root.is_absolute()
    assert settings.state_db is not None and settings.state_db.is_absolute()
    assert settings.market_config is not None and settings.market_config.is_absolute()
    assert settings.scoring_config is not None and settings.scoring_config.is_absolute()
    assert settings.schema_root is not None and settings.schema_root.is_absolute()
    assert settings.discovery_providers == (
        "openstreetmap-overpass",
        "openstreetmap-nominatim",
        "serper",
    )
    assert "never-return-this-value" not in repr(settings)


def test_health_probe_timeout_from_environment_is_bounded(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENCLAW_WEB_HEALTH_PROBE_TIMEOUT_SECONDS", "999")
    monkeypatch.setenv("OPENCLAW_WEB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    settings = HealthSettings.from_environment()

    assert settings.external_probe_timeout_seconds == 60.0

    monkeypatch.setenv("OPENCLAW_WEB_HEALTH_PROBE_TIMEOUT_SECONDS", "invalid")
    assert (
        HealthSettings.from_environment().external_probe_timeout_seconds == 15.0
    )


def test_health_always_recognizes_overpass_without_optional_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.setenv("OPENCLAW_WEB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    settings = HealthSettings.from_environment()

    assert settings.discovery_providers == (
        "openstreetmap-overpass",
        "openstreetmap-nominatim",
    )


def test_health_environment_does_not_normalize_relative_artifact_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENCLAW_WEB_ARTIFACT_ROOT", "relative-artifacts")

    settings = HealthSettings.from_environment()
    report = HealthService(settings, executable=lambda _name: None).check()

    assert not settings.artifact_root.is_absolute()
    assert not report.checks["artifact_root_absolute"]
    assert not (tmp_path / "relative-artifacts").exists()


def test_redaction_removes_secrets_from_structured_logs() -> None:
    assert "secret" not in redact({"token": "secret", "message": "ok"})


def test_retention_never_removes_approved_artifacts_or_backups(tmp_path: Path) -> None:
    rejected = tmp_path / "rejected" / "p1" / "screenshots" / "old.png"
    approved = tmp_path / "approved" / "p1" / "screenshot.png"
    backup = tmp_path / "backups" / "state.sqlite"
    for path in (rejected, approved, backup):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    old = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    import os
    os.utime(rejected, (old, old)); os.utime(approved, (old, old)); os.utime(backup, (old, old))
    apply_retention(tmp_path)
    assert not rejected.exists()
    assert approved.exists()
    assert backup.exists()
