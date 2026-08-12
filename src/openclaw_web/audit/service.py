"""Composition service for built-in and optional Lighthouse audit results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from openclaw_web.audit.builtin import (
    AuditFinding,
    BuiltinAuditResult,
    PageAuditObservation,
    audit_page,
)
from openclaw_web.audit.lighthouse import LighthouseRunner, LighthouseRunResult


@dataclass(frozen=True, slots=True)
class TechnicalAuditResult:
    status: Literal["complete", "partial"]
    findings: tuple[AuditFinding, ...]
    builtin: BuiltinAuditResult
    lighthouse: LighthouseRunResult


class AuditService:
    """Combine mandatory built-in evidence with an optional Lighthouse report."""

    def __init__(self, lighthouse: LighthouseRunner) -> None:
        self._lighthouse = lighthouse

    def audit(self, observation: PageAuditObservation) -> TechnicalAuditResult:
        builtin = audit_page(observation)
        lighthouse = self._lighthouse.run(observation.page.url)
        status: Literal["complete", "partial"] = (
            "complete"
            if builtin.status == "complete" and lighthouse.status == "complete"
            else "partial"
        )
        return TechnicalAuditResult(status, builtin.findings, builtin, lighthouse)
