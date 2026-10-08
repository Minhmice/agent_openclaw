"""Contract-first models and deterministic gates for Lead Intelligence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openclaw_web.models import Confidence, NonBlankString, PersistedTimestamp, StrictModel, WebUrl

SCHEMA_VERSION = "lead-intelligence.contracts.v1"
GATE_THRESHOLDS: Mapping[str, int] = {
    "BusinessStrength": 60,
    "AgencyFit": 65,
    "DigitalGap": 55,
}

Score = Annotated[float, Field(strict=True, ge=0, le=100)]
ConfidenceScore = Annotated[float, Field(strict=True, ge=0, le=1)]
ALLOWLISTED_SCREENSHOT_ASSETS = frozenset(
    {"screenshot.png", "screenshot.jpg", "screenshot.jpeg", "screenshot.webp"}
)


def _validate_screenshot_asset_name(value: str | None) -> str | None:
    if value is not None and value not in ALLOWLISTED_SCREENSHOT_ASSETS:
        raise ValueError("screenshot_asset must be an allowlisted filename")
    return value


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_CANDIDATE_DEFENSIBLE = "no_candidate_defensible"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    BLOCKED = "blocked"


class RedTeamVerdict(str, Enum):
    SURVIVE = "survive"
    DOWNGRADE = "downgrade"
    REJECT = "reject"


class PortfolioEntryState(str, Enum):
    RANKED = "ranked"
    SELECTED = "selected"
    WATCHING = "watching"
    REJECTED = "rejected"
    AWAITING_COMMAND = "awaiting-command"
    APPROVED = "approved"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"


class LeadScores(StrictModel):
    """Named, non-averaged scores used by the lead engine."""

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    business_strength: Score
    agency_fit: Score
    digital_gap: Score
    conversion_gap: Score = 0
    ux_gap: Score = 0
    trust_gap: Score = 0
    commercial_opportunity: Score
    dealability: Score
    evidence_confidence: ConfidenceScore


class GateResult(StrictModel):
    """Independent AND gate result; no average can compensate for a failure."""

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    schema_version: NonBlankString = SCHEMA_VERSION
    business_strength: Score
    agency_fit: Score
    digital_gap: Score
    thresholds: dict[str, int] = Field(
        default_factory=lambda: dict(GATE_THRESHOLDS),
        frozen=True,
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> GateResult:
        if self.thresholds != dict(GATE_THRESHOLDS):
            raise ValueError("gate thresholds are immutable and must use the canonical contract")
        return self

    @computed_field(return_type=bool)  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return (
            self.business_strength >= self.thresholds["BusinessStrength"]
            and self.agency_fit >= self.thresholds["AgencyFit"]
            and self.digital_gap >= self.thresholds["DigitalGap"]
        )

    @computed_field(return_type=tuple[str, ...])  # type: ignore[prop-decorator]
    @property
    def failed_dimensions(self) -> tuple[str, ...]:
        values = {
            "BusinessStrength": self.business_strength,
            "AgencyFit": self.agency_fit,
            "DigitalGap": self.digital_gap,
        }
        return tuple(name for name, value in values.items() if value < self.thresholds[name])


class StageOutcome(StrictModel):
    """Resumable structured result emitted by every logical stage."""

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    schema_version: NonBlankString = SCHEMA_VERSION
    stage_id: NonBlankString
    run_id: NonBlankString
    producer_role: NonBlankString
    status: StageStatus
    attempt_count: int = Field(strict=True, ge=0, le=5)
    started_at: PersistedTimestamp | None = None
    completed_at: PersistedTimestamp | None = None
    input_refs: tuple[NonBlankString, ...]
    output_refs: tuple[NonBlankString, ...]
    evidence_ids: tuple[NonBlankString, ...]
    confidence: Confidence | ConfidenceScore | None = None
    error_code: NonBlankString | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    reused: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_shape(self) -> StageOutcome:
        if (
            self.completed_at is not None
            and self.started_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at must not be before started_at")
        if self.status in {StageStatus.COMPLETE, StageStatus.PARTIAL} and self.completed_at is None:
            raise ValueError("completed stage outcomes require completed_at")
        _validate_checkpoint(self.checkpoint)
        return self


class LeadAssessment(StrictModel):
    """Evidence-linked pre-outreach lead assessment."""

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    schema_version: NonBlankString = SCHEMA_VERSION
    candidate_id: NonBlankString
    project_id: NonBlankString | None = None
    company_name: NonBlankString
    website_url: WebUrl | None = None
    business_strength: Score
    agency_fit: Score
    digital_gap: Score
    conversion_gap: Score = 0
    ux_gap: Score = 0
    trust_gap: Score = 0
    commercial_opportunity: Score
    dealability: Score
    evidence_confidence: ConfidenceScore
    evidence_ids: tuple[NonBlankString, ...] = ()
    red_team_verdict: RedTeamVerdict | None = None
    red_team_reason: NonBlankString | None = None
    top_issues: tuple[NonBlankString, ...] = ()
    public_contact: NonBlankString | None = None
    screenshot_asset: NonBlankString | None = None
    outreach_occurred: bool = Field(default=False, strict=True)

    @field_validator("screenshot_asset")
    @classmethod
    def validate_screenshot_asset(cls, value: str | None) -> str | None:
        return _validate_screenshot_asset_name(value)

    @property
    def gate(self) -> GateResult:
        return evaluate_gates(
            business_strength=self.business_strength,
            agency_fit=self.agency_fit,
            digital_gap=self.digital_gap,
        )

    @property
    def scores(self) -> LeadScores:
        return LeadScores(
            business_strength=self.business_strength,
            agency_fit=self.agency_fit,
            digital_gap=self.digital_gap,
            conversion_gap=self.conversion_gap,
            ux_gap=self.ux_gap,
            trust_gap=self.trust_gap,
            commercial_opportunity=self.commercial_opportunity,
            dealability=self.dealability,
            evidence_confidence=self.evidence_confidence,
        )

    @model_validator(mode="after")
    def validate_pre_outreach_claims(self) -> LeadAssessment:
        # This model intentionally has no buyer_intent/engagement fields.  The
        # validator also prevents future extensions from silently using them.
        if not self.outreach_occurred and self.red_team_reason:
            _validate_no_intent_keys({"red_team_reason": self.red_team_reason})
        return self


class PortfolioEntry(StrictModel):
    """A bounded portfolio item waiting for an explicit human command."""

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    schema_version: NonBlankString = SCHEMA_VERSION
    entry_id: NonBlankString
    portfolio_id: NonBlankString
    candidate_id: NonBlankString
    project_id: NonBlankString | None = None
    rank: int = Field(strict=True, ge=1, le=7)
    company_name: NonBlankString
    website_url: WebUrl | None = None
    business_strength: Score
    agency_fit: Score
    digital_gap: Score
    evidence_confidence: ConfidenceScore
    red_team_verdict: RedTeamVerdict
    conversion_gap: Score = 0
    ux_gap: Score = 0
    trust_gap: Score = 0
    commercial_opportunity: Score = 0
    dealability: Score = 0
    state: PortfolioEntryState = PortfolioEntryState.RANKED
    state_version: int = Field(default=0, strict=True, ge=0)
    top_issues: tuple[NonBlankString, ...] = ()
    public_contact: NonBlankString | None = None
    screenshot_asset: NonBlankString | None = None
    next_action: NonBlankString = "human-approval"

    @field_validator("screenshot_asset")
    @classmethod
    def validate_screenshot_asset(cls, value: str | None) -> str | None:
        return _validate_screenshot_asset_name(value)

    @model_validator(mode="after")
    def reject_red_team_reject(self) -> PortfolioEntry:
        if self.red_team_verdict is RedTeamVerdict.REJECT:
            raise ValueError("red-team reject cannot become a portfolio entry")
        return self


class PortfolioDecision(StrictModel):
    """Deterministic bounded selection result."""

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    schema_version: NonBlankString = SCHEMA_VERSION
    status: StageStatus
    selected: tuple[LeadAssessment, ...] = ()
    evaluated: int = Field(strict=True, ge=0)
    target: int = Field(default=5, strict=True, ge=3, le=7)
    maximum: int = Field(default=7, strict=True, ge=3, le=7)

    @model_validator(mode="after")
    def validate_bounds(self) -> PortfolioDecision:
        if len(self.selected) > self.maximum:
            raise ValueError("portfolio cannot exceed maximum size")
        if self.status is StageStatus.COMPLETE and not 3 <= len(self.selected) <= self.maximum:
            raise ValueError("complete portfolio must contain between 3 and 7 survivors")
        if self.status is StageStatus.PARTIAL and len(self.selected) >= 3:
            raise ValueError("partial portfolio is reserved for fewer than 3 defensible survivors")
        return self


def evaluate_gates(*, business_strength: float, agency_fit: float, digital_gap: float) -> GateResult:
    """Evaluate the three independent qualification gates."""

    return GateResult(
        business_strength=business_strength,
        agency_fit=agency_fit,
        digital_gap=digital_gap,
    )


def select_defensible_survivors(
    assessments: tuple[LeadAssessment, ...] | list[LeadAssessment],
) -> tuple[LeadAssessment, ...]:
    """Return gate-passing, evidence-backed, non-rejected assessments only."""

    return tuple(
        assessment
        for assessment in assessments
        if assessment.gate.passed
        and bool(assessment.evidence_ids)
        and assessment.evidence_confidence > 0
        and assessment.red_team_verdict in {RedTeamVerdict.SURVIVE, RedTeamVerdict.DOWNGRADE}
    )


def choose_portfolio(
    assessments: tuple[LeadAssessment, ...] | list[LeadAssessment],
    *,
    target: int = 5,
    maximum: int = 7,
) -> PortfolioDecision:
    """Select top defensible leads without fabricating missing candidates."""

    if isinstance(target, bool) or not 3 <= target <= 7:
        raise ValueError("target must be between 3 and 7")
    if isinstance(maximum, bool) or not target <= maximum <= 7:
        raise ValueError("maximum must be between target and 7")
    survivors = sorted(
        select_defensible_survivors(assessments),
        key=lambda item: (
            item.commercial_opportunity,
            item.digital_gap,
            item.dealability,
            item.evidence_confidence,
        ),
        reverse=True,
    )
    selected = tuple(survivors[:maximum])
    if not selected:
        status = StageStatus.NO_CANDIDATE_DEFENSIBLE
    elif len(selected) < 3:
        status = StageStatus.PARTIAL
    else:
        status = StageStatus.COMPLETE
    return PortfolioDecision(status=status, selected=selected, evaluated=len(assessments), target=target, maximum=maximum)


def validate_pre_outreach_payload(payload: Mapping[str, Any]) -> None:
    """Reject intent-like fields until an actual outreach event exists."""

    _validate_no_intent_keys(payload)


_INTENT_KEYS = re.compile(r"(?:^|[_-])(buyer[_-]?intent|engagement|intent)(?:$|[_-])", re.IGNORECASE)


def _validate_no_intent_keys(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            if _INTENT_KEYS.search(name):
                raise ValueError(f"pre-outreach payload must not contain {name}")
            _validate_no_intent_keys(child, path=f"{path}.{name}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_no_intent_keys(child, path=f"{path}[{index}]")


_CHECKPOINT_KEYS = frozenset(
    {
        "cursor",
        "offset",
        "next_candidate_index",
        "completed_candidate_ids",
        "input_hash",
        "output_hash",
        "attempt",
        "stage_index",
        "provider_failures",
        "last_completed_stage",
    }
)
_SENSITIVE_KEY = re.compile(r"(?:password|api[_-]?key|token|cookie|session|private[_-]?key|secret)", re.IGNORECASE)


def _validate_checkpoint(value: Mapping[str, Any]) -> None:
    for key, child in value.items():
        name = str(key)
        if name not in _CHECKPOINT_KEYS or _SENSITIVE_KEY.search(name):
            raise ValueError(f"checkpoint contains a non-allowlisted field: {name}")
        _validate_checkpoint_value(child)


def _validate_checkpoint_value(value: Any) -> None:
    """Keep resumable values primitive and reject secret-like content."""

    if isinstance(value, Mapping):
        _validate_checkpoint(value)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_checkpoint_value(item)
        return
    if isinstance(value, str):
        if _SENSITIVE_KEY.search(value):
            raise ValueError("checkpoint contains a secret-like value")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise ValueError("checkpoint contains a non-serializable value")


def export_lead_schemas(output_dir: Path) -> None:
    """Write deterministic JSON Schemas for the new contracts."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    models: dict[str, type[BaseModel] | None] = {
        "agent_topology.json": None,
        "lead_assessment.json": LeadAssessment,
        "gate_result.json": GateResult,
        "portfolio_entry.json": PortfolioEntry,
        "stage_outcome.json": StageOutcome,
        "portfolio_decision.json": PortfolioDecision,
    }
    for filename, model in models.items():
        if model is None:
            # Import lazily to keep topology and contracts independently usable.
            from openclaw_web.topology import AgentTopology

            model = AgentTopology
        schema = model.model_json_schema(mode="serialization")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        (destination / filename).write_text(
            json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


__all__ = [
    "ALLOWLISTED_SCREENSHOT_ASSETS",
    "GATE_THRESHOLDS",
    "SCHEMA_VERSION",
    "GateResult",
    "LeadAssessment",
    "LeadScores",
    "PortfolioDecision",
    "PortfolioEntry",
    "PortfolioEntryState",
    "RedTeamVerdict",
    "StageOutcome",
    "StageStatus",
    "choose_portfolio",
    "evaluate_gates",
    "export_lead_schemas",
    "select_defensible_survivors",
    "validate_pre_outreach_payload",
]


if __name__ == "__main__":
    export_lead_schemas(Path("schemas/generated"))
