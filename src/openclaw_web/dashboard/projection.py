"""Allowlisted projection from workflow persistence to dashboard JSON."""

from __future__ import annotations

from datetime import UTC, datetime

from openclaw_web.topology import CANONICAL_STAGE_FLOW

from .models import DashboardSnapshot
from .read_repository import DashboardReadRepository


def project_dashboard(repository: DashboardReadRepository) -> DashboardSnapshot:
    """Build a sanitized snapshot; raw database snapshots never cross this boundary."""

    runs = repository.list_runs(limit=1)
    leads = repository.list_leads(limit=50)
    events = repository.list_events(limit=50)
    counts = repository.counts()
    current = runs[0] if runs else None
    partial = bool(current and current.status in {"partial", "failed_retryable", "failed-retryable"})
    blocked = bool(current and current.status == "blocked")
    status = (
        "blocked"
        if blocked
        else "partial"
        if partial
        else "failed_terminal"
        if current and current.status in {"failed_terminal", "failed-terminal"}
        else "healthy"
    )
    return DashboardSnapshot(
        service_status=status,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        freshness="fresh",
        stale=False,
        current_run=current,
        current_stage=None if current is None else current.current_stage,
        stage_rail=CANONICAL_STAGE_FLOW,
        funnel=counts,
        portfolio=leads,
        events=events,
        partial=partial,
        blocked=blocked,
        error=None,
    )


__all__ = ["project_dashboard"]
