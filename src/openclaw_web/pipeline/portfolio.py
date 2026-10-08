"""Bounded portfolio selection and single-message delivery formatting."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from openclaw_web.lead_contracts import (
    LeadAssessment,
    PortfolioEntry,
    PortfolioEntryState,
    RedTeamVerdict,
    StageStatus,
    choose_portfolio,
)


@dataclass(frozen=True, slots=True)
class PortfolioSelection:
    portfolio_id: str
    status: StageStatus
    entries: tuple[PortfolioEntry, ...]
    evaluated_candidates: int


@dataclass(frozen=True, slots=True)
class PortfolioDelivery:
    portfolio_id: str
    idempotency_key: str
    message: str
    created_at: datetime


class PortfolioRepository(Protocol):
    def create_portfolio(
        self,
        *,
        portfolio_id: str,
        run_id: str | None,
        status: str,
        entries: tuple[PortfolioEntry, ...],
        target: int,
        maximum: int,
    ) -> bool: ...


class PortfolioManager:
    """Select and optionally persist a bounded portfolio."""

    def __init__(self, *, target: int = 5, maximum: int = 7) -> None:
        if isinstance(target, bool) or not 3 <= target <= 7:
            raise ValueError("target must be between 3 and 7")
        if isinstance(maximum, bool) or not target <= maximum <= 7:
            raise ValueError("maximum must be between target and 7")
        self.target = target
        self.maximum = maximum

    def select(
        self,
        portfolio_id: str,
        assessments: list[LeadAssessment] | tuple[LeadAssessment, ...],
    ) -> PortfolioSelection:
        decision = choose_portfolio(
            assessments,
            target=self.target,
            maximum=self.maximum,
        )
        entries = tuple(
            _entry_from_assessment(portfolio_id, index, assessment)
            for index, assessment in enumerate(decision.selected, start=1)
        )
        return PortfolioSelection(
            portfolio_id=portfolio_id,
            status=decision.status,
            entries=entries,
            evaluated_candidates=decision.evaluated,
        )

    def persist(
        self,
        repository: PortfolioRepository,
        selection: PortfolioSelection,
        *,
        run_id: str | None,
    ) -> bool:
        return repository.create_portfolio(
            portfolio_id=selection.portfolio_id,
            run_id=run_id,
            status=selection.status.value,
            entries=selection.entries,
            target=self.target,
            maximum=self.maximum,
        )

    @staticmethod
    def delivery(
        selection: PortfolioSelection,
        *,
        dashboard_url: str,
        created_at: datetime | None = None,
    ) -> PortfolioDelivery:
        return PortfolioDelivery(
            portfolio_id=selection.portfolio_id,
            idempotency_key=f"portfolio:{selection.portfolio_id}",
            message=render_portfolio_summary(selection.entries, dashboard_url=dashboard_url),
            created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
        )


def _entry_from_assessment(
    portfolio_id: str,
    rank: int,
    assessment: LeadAssessment,
) -> PortfolioEntry:
    entry_id = f"entry-{uuid.uuid5(uuid.NAMESPACE_URL, f'{portfolio_id}:{assessment.candidate_id}').hex}"
    return PortfolioEntry(
        entry_id=entry_id,
        portfolio_id=portfolio_id,
        candidate_id=assessment.candidate_id,
        project_id=assessment.project_id,
        rank=rank,
        company_name=assessment.company_name,
        website_url=assessment.website_url,
        business_strength=assessment.business_strength,
        agency_fit=assessment.agency_fit,
        digital_gap=assessment.digital_gap,
        evidence_confidence=assessment.evidence_confidence,
        red_team_verdict=assessment.red_team_verdict or RedTeamVerdict.DOWNGRADE,
        conversion_gap=assessment.conversion_gap,
        ux_gap=assessment.ux_gap,
        trust_gap=assessment.trust_gap,
        commercial_opportunity=assessment.commercial_opportunity,
        dealability=assessment.dealability,
        state=PortfolioEntryState.AWAITING_COMMAND,
        state_version=0,
        top_issues=assessment.top_issues,
        public_contact=assessment.public_contact,
        screenshot_asset=assessment.screenshot_asset,
        next_action="human-approval",
    )


def render_portfolio_summary(
    entries: tuple[PortfolioEntry, ...] | list[PortfolioEntry],
    *,
    dashboard_url: str,
) -> str:
    """Render one bounded Discord portfolio message with typed fallbacks."""

    values = tuple(entries)
    if not 3 <= len(values) <= 7:
        raise ValueError("portfolio summary must contain between 3 and 7 entries")
    if not isinstance(dashboard_url, str) or not dashboard_url.strip():
        raise ValueError("dashboard_url must be a non-blank string")
    portfolio_ids = {entry.portfolio_id for entry in values}
    if len(portfolio_ids) != 1:
        raise ValueError("portfolio entries must share one portfolio_id")
    portfolio_id = next(iter(portfolio_ids))
    rows = []
    for entry in values:
        url = str(entry.website_url) if entry.website_url is not None else "(URL unavailable)"
        if entry.project_id is not None:
            fallback = (
                f"`/lead-approve {entry.project_id}` or "
                f"`/lead-request-change {entry.project_id} <note>`"
            )
        else:
            fallback = "Dashboard (project mapping chưa xác minh; không phát lệnh đoán)"
        rows.append(
            f"{entry.rank}. **{entry.company_name}** — {url}\n"
            f"   Business {entry.business_strength:.0f} · Agency {entry.agency_fit:.0f} · "
            f"Digital gap {entry.digital_gap:.0f} · Evidence {entry.evidence_confidence:.0%}\n"
            f"   Red Team: `{entry.red_team_verdict.value}` · state `{entry.state.value}`\n"
            f"   Fallback: {fallback}"
        )
    return (
        f"**Lead Intelligence portfolio `{portfolio_id}`**\n\n"
        + "\n\n".join(rows)
        + f"\n\nDashboard: {dashboard_url}\n"
        "Mỗi lead đang chờ human action; selection không tự chạy redesign."
    )


__all__ = [
    "PortfolioDelivery",
    "PortfolioManager",
    "PortfolioRepository",
    "PortfolioSelection",
    "render_portfolio_summary",
]
