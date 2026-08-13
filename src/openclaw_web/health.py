"""Bounded, redacted readiness checks for the production workflow."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    ready: bool
    detail: str | None = None


Probe = Callable[[tuple[str, ...], float], ProbeResult]
ExecutableLookup = Callable[[str], str | None]
BrowserCheck = Callable[[], bool]


@dataclass(slots=True)
class HealthSettings:
    artifact_root: Path
    state_db: Path | None = None
    market_config: Path | None = None
    scoring_config: Path | None = None
    schema_root: Path | None = None
    discovery_providers: tuple[str, ...] = ()
    external_probe_timeout_seconds: float = 5.0

    @classmethod
    def from_environment(cls) -> HealthSettings:
        package_root = Path(__file__).resolve().parents[2]

        def configured(name: str, default: Path) -> Path:
            value = os.environ.get(name)
            return Path(value).expanduser() if value else default.resolve()

        providers = ("openstreetmap-overpass",) + tuple(
            name
            for name, variable in (
                ("google-places", "GOOGLE_PLACES_API_KEY"),
                ("serper", "SERPER_API_KEY"),
            )
            if os.environ.get(variable)
        )
        return cls(
            artifact_root=configured(
                "OPENCLAW_WEB_ARTIFACT_ROOT",
                Path.home() / ".local/share/openclaw-web/artifacts",
            ),
            state_db=configured(
                "OPENCLAW_WEB_STATE_DB",
                Path.home() / ".local/state/openclaw-web/state.sqlite",
            ),
            market_config=configured(
                "OPENCLAW_WEB_MARKET_CONFIG",
                package_root / "config/markets/hanoi-80km.yaml",
            ),
            scoring_config=configured(
                "OPENCLAW_WEB_SCORING_CONFIG",
                package_root / "config/scoring/base-v1.yaml",
            ),
            schema_root=configured(
                "OPENCLAW_WEB_SCHEMA_ROOT", package_root / "schemas"
            ),
            discovery_providers=providers,
        )


@dataclass(frozen=True, slots=True)
class HealthReport:
    manual_audit_ready: bool
    discovery_ready: bool
    checks: dict[str, bool]


def _subprocess_probe(command: tuple[str, ...], timeout: float) -> ProbeResult:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=False,
            shell=False,
            timeout=timeout,
        )
        return ProbeResult(command[0], completed.returncode == 0)
    except (OSError, subprocess.TimeoutExpired):
        return ProbeResult(command[0], False)


def _playwright_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).is_file()
    except Exception:  # noqa: BLE001 - optional browser probe is fail-closed
        return False


class HealthService:
    def __init__(
        self,
        settings: HealthSettings,
        *,
        executable: ExecutableLookup = shutil.which,
        browser_ready: BrowserCheck = _playwright_ready,
        probe: Probe = _subprocess_probe,
    ) -> None:
        self.settings = settings
        self._executable = executable
        self._browser_ready = browser_ready
        self._probe = probe

    @staticmethod
    def _database_checks(path: Path | None) -> tuple[bool, bool, bool, bool]:
        if path is None or not path.is_absolute() or not path.is_file():
            return False, False, False, False
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            migration_row = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()
            last_run = connection.execute("SELECT 1 FROM runs LIMIT 1").fetchone()
            outbox_row = connection.execute(
                "SELECT COUNT(*) FROM deliveries WHERE status IN ('pending','failed','sending')"
            ).fetchone()
            connection.close()
            return (
                {"schema_migrations", "runs", "deliveries"}.issubset(tables),
                migration_row is not None and int(migration_row[0]) > 0,
                last_run is not None,
                outbox_row is not None,
            )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return False, False, False, False

    def check(self) -> HealthReport:
        artifact_root = Path(self.settings.artifact_root)
        database, migrations, last_run, outbox = self._database_checks(self.settings.state_db)
        timeout = self.settings.external_probe_timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
            timeout = 5.0
        openclaw = self._executable("openclaw") is not None
        lighthouse = self._executable("lighthouse") is not None
        systemctl = self._executable("systemctl") is not None
        probe_commands = {
            "openclaw_health": ("openclaw", "health"),
            "gateway": ("openclaw", "gateway", "status"),
            "discord": (
                "openclaw",
                "channels",
                "status",
                "--channel",
                "discord",
                "--probe",
            ),
            "timer": (
                "systemctl",
                "--user",
                "is-active",
                "openclaw-web-discovery.timer",
            ),
        }
        external = {
            name: self._probe(command, float(timeout)).ready
            if (command[0] == "openclaw" and openclaw)
            or (command[0] == "systemctl" and systemctl)
            else False
            for name, command in probe_commands.items()
        }
        browser = self._browser_ready()
        checks = {
            "artifact_root_absolute": artifact_root.is_absolute(),
            "artifact_root": artifact_root.is_absolute() and artifact_root.is_dir(),
            "database": database,
            "migrations": migrations,
            "market_config": self.settings.market_config is not None
            and self.settings.market_config.is_absolute()
            and self.settings.market_config.is_file(),
            "scoring_config": self.settings.scoring_config is not None
            and self.settings.scoring_config.is_absolute()
            and self.settings.scoring_config.is_file(),
            "schema_root": self.settings.schema_root is not None
            and self.settings.schema_root.is_absolute()
            and self.settings.schema_root.is_dir(),
            "browser": browser,
            "lighthouse": lighthouse,
            "audit_engine": browser and lighthouse,
            "overpass": "openstreetmap-overpass" in self.settings.discovery_providers,
            "openclaw_cli": openclaw,
            **external,
            "last_run": last_run,
            "outbox": outbox,
            "discovery_provider": bool(self.settings.discovery_providers),
        }
        manual_required = (
            "artifact_root",
            "database",
            "migrations",
            "market_config",
            "scoring_config",
            "schema_root",
            "browser",
            "lighthouse",
            "audit_engine",
        )
        discovery_required = (
            *manual_required,
            "discovery_provider",
            "overpass",
            "openclaw_health",
            "gateway",
            "discord",
            "timer",
        )
        return HealthReport(
            all(checks[name] for name in manual_required),
            all(checks[name] for name in discovery_required),
            checks,
        )
