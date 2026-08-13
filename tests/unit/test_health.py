from datetime import UTC, datetime, timedelta
from pathlib import Path

from openclaw_web.health import HealthService, HealthSettings
from openclaw_web.observability import apply_retention, redact


def test_health_reports_missing_discovery_provider_without_breaking_manual_audit(tmp_path: Path) -> None:
    report = HealthService(HealthSettings(artifact_root=tmp_path, discovery_providers=())).check()
    assert report.manual_audit_ready
    assert not report.discovery_ready


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
