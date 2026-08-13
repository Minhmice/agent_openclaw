"""Offline-safe readiness checks; optional integrations never block manual audit."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class HealthSettings:
    artifact_root: Path
    discovery_providers: tuple[str, ...] = ()
@dataclass(frozen=True, slots=True)
class HealthReport:
    manual_audit_ready: bool
    discovery_ready: bool
    checks: dict[str, bool]
class HealthService:
    def __init__(self, settings: HealthSettings) -> None: self.settings = settings
    def check(self) -> HealthReport:
        artifact_root = Path(self.settings.artifact_root); artifact_root.mkdir(parents=True, exist_ok=True)
        checks = {"artifact_root": artifact_root.is_dir(), "discovery_provider": bool(self.settings.discovery_providers)}
        return HealthReport(checks["artifact_root"], checks["artifact_root"] and checks["discovery_provider"], checks)
