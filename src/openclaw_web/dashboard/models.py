"""Sanitized public models for the live product dashboard."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from openclaw_web.models import NonBlankString, StrictModel, WebUrl


class DashboardAction(str, Enum):
    SELECT_LEAD = "select-lead"
    WATCH_LEAD = "watch-lead"
    LEAD_APPROVE = "lead-approve"
    LEAD_REJECT = "lead-reject"
    LEAD_REQUEST_CHANGE = "lead-request-change"
    PAGE_STATUS = "page-status"
    PAGE_DONE = "page-done"
    PAGE_APPROVE = "page-approve"
    BLOCK = "block"
    FINAL_CONFIRM = "final-confirm"

    def __str__(self) -> str:
        return self.value


class DashboardPage(StrictModel):
    """Allowlisted page projection used for assigned page actions."""

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    page_slug: NonBlankString
    status: NonBlankString
    assigned_actor: NonBlankString | None = None
    checklist_complete: bool | None = Field(default=None, strict=True)
    next_action: NonBlankString | None = None


class DashboardLead(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    entry_id: NonBlankString
    portfolio_id: NonBlankString
    candidate_id: NonBlankString
    project_id: NonBlankString | None = None
    project_state_version: int | None = Field(default=None, strict=True, ge=0)
    rank: int = Field(strict=True, ge=1, le=7)
    company_name: NonBlankString
    website_url: WebUrl | None = None
    score_breakdown: dict[str, float]
    evidence_confidence: float = Field(strict=True, ge=0, le=1)
    red_team_verdict: NonBlankString
    top_issues: tuple[NonBlankString, ...] = ()
    public_contact: NonBlankString | None = None
    screenshot_url: NonBlankString | None = None
    pages: tuple[DashboardPage, ...] = ()
    state: NonBlankString
    state_version: int = Field(strict=True, ge=0)
    next_action: NonBlankString | None = None


class DashboardRun(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    run_id: NonBlankString
    status: NonBlankString
    current_stage: NonBlankString | None = None
    started_at: NonBlankString
    completed_at: NonBlankString | None = None
    evaluated_candidates: int = Field(default=0, strict=True, ge=0)
    provider_failures: tuple[NonBlankString, ...] = ()
    stages: tuple[dict[str, Any], ...] = ()


class DashboardEvent(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    event_id: NonBlankString
    event_type: NonBlankString
    target_id: NonBlankString
    action: NonBlankString | None = None
    actor: NonBlankString | None = None
    status: NonBlankString | None = None
    created_at: NonBlankString


class DashboardSnapshot(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    schema_version: NonBlankString = "dashboard.snapshot.v1"
    service_status: NonBlankString
    generated_at: NonBlankString
    freshness: NonBlankString
    stale: bool = Field(default=False, strict=True)
    current_run: DashboardRun | None = None
    current_stage: NonBlankString | None = None
    stage_rail: tuple[NonBlankString, ...]
    funnel: dict[str, int]
    portfolio: tuple[DashboardLead, ...] = ()
    events: tuple[DashboardEvent, ...] = ()
    partial: bool = Field(default=False, strict=True)
    blocked: bool = Field(default=False, strict=True)
    error: NonBlankString | None = None


class ActionRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    action: DashboardAction
    target_id: NonBlankString
    page_slug: NonBlankString | None = None
    reason: NonBlankString | None = None
    expected_state_version: int = Field(strict=True, ge=0)
    idempotency_key: NonBlankString = Field(max_length=200)

    @model_validator(mode="after")
    def validate_action_context(self) -> ActionRequest:
        page_actions = {
            DashboardAction.PAGE_STATUS,
            DashboardAction.PAGE_DONE,
            DashboardAction.PAGE_APPROVE,
            DashboardAction.BLOCK,
        }
        reason_actions = {
            DashboardAction.LEAD_REJECT,
            DashboardAction.LEAD_REQUEST_CHANGE,
            DashboardAction.BLOCK,
        }
        if self.action in page_actions and self.page_slug is None:
            raise ValueError("page_slug is required for page actions")
        if self.action in reason_actions and self.reason is None:
            raise ValueError("reason is required for this action")
        return self


class ActionResponse(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    status: NonBlankString
    event_id: NonBlankString
    target_id: NonBlankString
    state: NonBlankString
    state_version: int = Field(strict=True, ge=0)
    next_action: NonBlankString | None = None
    message_vi: NonBlankString


__all__ = [
    "ActionRequest",
    "ActionResponse",
    "DashboardAction",
    "DashboardEvent",
    "DashboardLead",
    "DashboardPage",
    "DashboardRun",
    "DashboardSnapshot",
]
