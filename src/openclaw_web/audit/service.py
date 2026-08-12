"""Composition service for built-in and optional Lighthouse audit results."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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

    def __init__(
        self,
        lighthouse: LighthouseRunner,
        *,
        reference_time_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._lighthouse = lighthouse
        self._reference_time_provider = reference_time_provider

    def audit(
        self,
        observation: PageAuditObservation,
        *,
        reference_time: datetime | None = None,
    ) -> TechnicalAuditResult:
        effective_reference_time = (
            reference_time
            if reference_time is not None
            else self._reference_time_provider()
            if self._reference_time_provider is not None
            else None
        )
        if effective_reference_time is not None and (
            not isinstance(effective_reference_time, datetime)
            or effective_reference_time.tzinfo is None
            or effective_reference_time.utcoffset() is None
        ):
            raise ValueError("reference_time must be timezone-aware")
        builtin = audit_page(observation, now=effective_reference_time)
        lighthouse = self._lighthouse.run(observation.page.url)
        status: Literal["complete", "partial"] = (
            "complete"
            if builtin.status == "complete" and lighthouse.status == "complete"
            else "partial"
        )
        return TechnicalAuditResult(status, builtin.findings, builtin, lighthouse)
