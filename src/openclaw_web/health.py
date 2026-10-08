"""Bounded, redacted readiness checks for the production workflow."""

from __future__ import annotations

import math
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

DEFAULT_EXTERNAL_PROBE_TIMEOUT_SECONDS = 15.0
MIN_EXTERNAL_PROBE_TIMEOUT_SECONDS = 0.1
MAX_EXTERNAL_PROBE_TIMEOUT_SECONDS = 60.0
HEALTH_PROBE_TIMEOUT_ENV = "OPENCLAW_WEB_HEALTH_PROBE_TIMEOUT_SECONDS"

# Only these values can cross the probe boundary. Command output is never
# copied into a health report because it may contain paths, diagnostics, or
# provider data that does not belong in a readiness response.
_SAFE_PROBE_DETAILS = frozenset(
    {
        "ok",
        "timeout",
        "unavailable",
        "failed",
        "enabled",
        "disabled",
        "active",
        "inactive",
        "not-found",
        "masked",
        "static",
        "indirect",
        "generated",
    }
)


@dataclass(slots=True)
class HealthSettings:
    artifact_root: Path
    state_db: Path | None = None
    market_config: Path | None = None
    scoring_config: Path | None = None
    schema_root: Path | None = None
    discovery_providers: tuple[str, ...] = ()
    external_probe_timeout_seconds: float = DEFAULT_EXTERNAL_PROBE_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> HealthSettings:
        package_root = Path(__file__).resolve().parents[2]

        def configured(name: str, default: Path) -> Path:
            value = os.environ.get(name)
            return Path(value).expanduser() if value else default.resolve()

        providers = ("openstreetmap-overpass", "openstreetmap-nominatim") + tuple(
            name
            for name, variable in (
                ("google-places", "GOOGLE_PLACES_API_KEY"),
                ("serper", "SERPER_API_KEY"),
            )
            if os.environ.get(variable)
        )
        probe_timeout = _bounded_probe_timeout(
            os.environ.get(HEALTH_PROBE_TIMEOUT_ENV),
            default=DEFAULT_EXTERNAL_PROBE_TIMEOUT_SECONDS,
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
            external_probe_timeout_seconds=probe_timeout,
        )


@dataclass(frozen=True, slots=True)
class HealthReport:
    manual_audit_ready: bool
    discovery_ready: bool
    checks: dict[str, bool]
    discovery_blockers: tuple[str, ...] = ()


def _bounded_probe_timeout(
    value: object,
    *,
    default: float = DEFAULT_EXTERNAL_PROBE_TIMEOUT_SECONDS,
) -> float:
    """Return a finite, positive timeout suitable for a health subprocess."""

    if isinstance(value, bool):
        return default
    if not isinstance(value, (str, int, float)):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed) or parsed <= 0:
        return default
    return min(
        max(parsed, MIN_EXTERNAL_PROBE_TIMEOUT_SECONDS),
        MAX_EXTERNAL_PROBE_TIMEOUT_SECONDS,
    )


def _safe_probe_detail(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized if normalized in _SAFE_PROBE_DETAILS else None


def _status_from_output(*streams: bytes) -> str | None:
    for stream in streams:
        if not stream:
            continue
        text = stream.decode("utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            status = _safe_probe_detail(stripped.split(maxsplit=1)[0])
            if status is not None:
                return status
    return None


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
        if completed.returncode == 0:
            return ProbeResult(
                command[0],
                True,
                detail=_status_from_output(completed.stdout, completed.stderr) or "ok",
            )
        return ProbeResult(
            command[0],
            False,
            detail=_status_from_output(completed.stdout, completed.stderr) or "failed",
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(command[0], False, detail="timeout")
    except OSError:
        return ProbeResult(command[0], False, detail="unavailable")


def _browser_executable_candidates() -> tuple[Path, ...]:
    configured = os.environ.get("CHROME_PATH")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())

    configured_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    roots: list[Path] = []
    if configured_root and configured_root != "0":
        roots.append(Path(configured_root).expanduser())
    else:
        roots.extend(
            (
                Path.home() / ".cache/ms-playwright",
                Path.home() / "AppData/Local/ms-playwright",
                Path.home() / "Library/Caches/ms-playwright",
            )
        )
        if configured_root == "0":
            roots.append(Path(__file__).resolve().parents[2] / ".local-browsers")

    patterns = (
        "*/chrome-linux64/chrome",
        "*/chrome-linux/chrome",
        "*/chrome-win/chrome.exe",
        "*/chrome-win64/chrome.exe",
        "*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "*/chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    )
    for root in roots:
        for pattern in patterns:
            candidates.extend(root.glob(pattern))
    return tuple(candidates)


def _playwright_ready() -> bool:
    """Check the managed browser without starting Playwright's driver process."""

    try:
        return any(path.is_file() for path in _browser_executable_candidates())
    except (OSError, RuntimeError, ValueError):
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
        timeout = _bounded_probe_timeout(self.settings.external_probe_timeout_seconds)
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
            "timer_enabled": (
                "systemctl",
                "--user",
                "is-enabled",
                "openclaw-web-discovery.timer",
            ),
            "timer_active": (
                "systemctl",
                "--user",
                "is-active",
                "openclaw-web-discovery.timer",
            ),
        }
        probe_results: dict[str, ProbeResult] = {}
        for name, command in probe_commands.items():
            can_probe = (command[0] == "openclaw" and openclaw) or (
                command[0] == "systemctl" and systemctl
            )
            probe_results[name] = (
                self._probe(command, float(timeout))
                if can_probe
                else ProbeResult(name, False, detail="unavailable")
            )
        external = {name: result.ready for name, result in probe_results.items()}
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
            # Keep `timer` as a compatibility alias for the old active-state
            # check while exposing the two systemd dimensions separately.
            "timer": external["timer_active"],
            "timer_enabled": external["timer_enabled"],
            "timer_active": external["timer_active"],
            "last_run": last_run,
            "last_run_present": last_run,
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
            "database",
            "migrations",
            "discovery_provider",
            "overpass",
            "openclaw_health",
            "gateway",
            "discord",
            "timer_enabled",
            "timer_active",
            "last_run_present",
        )
        blockers: list[str] = [
            f"{name}_missing"
            for name in manual_required
            if name not in {"database", "migrations"} and not checks[name]
        ]
        if not checks["database"]:
            blockers.append("database_unavailable")
        elif not checks["migrations"]:
            blockers.append("migrations_missing")
        elif not checks["last_run_present"]:
            blockers.append("last_run_missing")
        if not checks["discovery_provider"]:
            blockers.append("discovery_provider_missing")
        elif not checks["overpass"]:
            blockers.append("overpass_not_configured")
        for name in ("openclaw_health", "gateway", "discord"):
            if checks[name]:
                continue
            detail = _safe_probe_detail(probe_results[name].detail)
            if detail == "timeout":
                blockers.append(f"{name}_probe_timeout")
            elif detail == "unavailable":
                blockers.append(f"{name}_unavailable")
            else:
                blockers.append(f"{name}_unhealthy")
        if not checks["timer_enabled"]:
            detail = _safe_probe_detail(probe_results["timer_enabled"].detail)
            if detail == "disabled":
                blockers.append("timer_disabled")
            elif detail == "not-found":
                blockers.append("timer_not_found")
            elif detail == "unavailable":
                blockers.append("systemctl_unavailable")
            else:
                blockers.append("timer_enablement_failed")
        elif not checks["timer_active"]:
            detail = _safe_probe_detail(probe_results["timer_active"].detail)
            if detail == "inactive":
                blockers.append("timer_inactive")
            elif detail == "not-found":
                blockers.append("timer_not_found")
            elif detail == "unavailable":
                blockers.append("systemctl_unavailable")
            else:
                blockers.append("timer_unhealthy")
        return HealthReport(
            all(checks[name] for name in manual_required),
            all(checks[name] for name in discovery_required),
            checks,
            tuple(dict.fromkeys(blockers)),
        )
