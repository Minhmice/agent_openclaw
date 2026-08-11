"""Canonical validated records for the website discovery and audit workflow."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

NonBlankString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
Sha256 = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$", strict=True),
]
StrictAwareDatetime = Annotated[AwareDatetime, Field(strict=True)]
StrictBool = Annotated[bool, Field(strict=True)]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class ClaimStatus(str, Enum):
    """How directly an evidence claim is supported."""

    OBSERVED = "observed"
    INFERRED = "inferred"
    ESTIMATED = "estimated"
    UNVERIFIED = "unverified"


class Confidence(str, Enum):
    """Confidence assigned to evidence or a derived result."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CandidateState(str, Enum):
    """Lifecycle state for a discovery candidate."""

    DISCOVERED = "discovered"
    GEOFENCED = "geofenced"
    PREFILTERED = "prefiltered"
    AUDITED = "audited"
    REVIEW_READY = "review-ready"
    GEOFENCE_REJECTED = "geofence-rejected"
    DUPLICATE = "duplicate"
    ROBOTS_BLOCKED = "robots-blocked"
    UNQUALIFIED = "unqualified"
    PARTIAL = "partial"
    FAILED_RETRYABLE = "failed-retryable"
    FAILED_TERMINAL = "failed-terminal"


class ProjectState(str, Enum):
    """Lifecycle state for a reviewed website project."""

    DISCOVERED = "discovered"
    REVIEW = "review"
    APPROVED = "approved"
    WEBSITE_BRIEF = "website-brief"
    TASK = "task"
    STAKEHOLDER_REVIEW = "stakeholder-review"
    OFFER_READY = "offer-ready"
    REJECTED = "rejected"


class PageState(str, Enum):
    """Lifecycle state for a project page."""

    PLANNED = "planned"
    CONTENT_DRAFT = "content-draft"
    CONTENT_READY = "content-ready"
    DESIGN_READY = "design-ready"
    QA_NEEDED = "qa-needed"
    STAKEHOLDER_REVIEW = "stakeholder-review"
    APPROVED = "approved"


class Severity(str, Enum):
    """Canonical issue priority."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class DeliveryState(str, Enum):
    """Outbound event delivery state."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class StrictModel(BaseModel):
    """Base configuration shared by canonical operator-facing records."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, validate_default=True)


class CandidateSeed(StrictModel):
    """Raw candidate discovered from an external source."""

    url: AnyHttpUrl
    business_name: NonBlankString
    source_url: AnyHttpUrl
    source_type: NonBlankString
    discovered_at: StrictAwareDatetime
    seed_id: NonBlankString | None = None
    address: NonBlankString | None = None
    latitude: float | None = Field(default=None, strict=True, allow_inf_nan=False, ge=-90, le=90)
    longitude: float | None = Field(
        default=None,
        strict=True,
        allow_inf_nan=False,
        ge=-180,
        le=180,
    )
    industry_hint: NonBlankString | None = None
    external_id: NonBlankString | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Candidate(StrictModel):
    """Normalized organization being evaluated by the workflow."""

    candidate_id: NonBlankString
    name: NonBlankString
    state: CandidateState = CandidateState.DISCOVERED
    seed_id: NonBlankString | None = None
    website_url: AnyHttpUrl | None = None
    canonical_domain: NonBlankString | None = None
    address: NonBlankString | None = None
    phone: NonBlankString | None = None
    industry: NonBlankString | None = None
    latitude: float | None = Field(default=None, strict=True, allow_inf_nan=False, ge=-90, le=90)
    longitude: float | None = Field(
        default=None,
        strict=True,
        allow_inf_nan=False,
        ge=-180,
        le=180,
    )
    discovered_at: StrictAwareDatetime | None = None
    updated_at: StrictAwareDatetime | None = None
    source_urls: list[AnyHttpUrl] = Field(default_factory=list)
    duplicate_of: NonBlankString | None = None


class PageRecord(StrictModel):
    """A fetched or planned web page associated with a candidate."""

    page_id: NonBlankString
    candidate_id: NonBlankString
    page_url: AnyHttpUrl
    page_type: NonBlankString
    status_code: int | None = Field(default=None, strict=True, ge=100, le=599)
    title: NonBlankString | None = None
    content_hash: Sha256 | None = None
    captured_at: StrictAwareDatetime | None = None
    robots_allowed: StrictBool | None = None


class Evidence(StrictModel):
    """A source-backed observation or explicitly qualified claim."""

    evidence_id: NonBlankString
    candidate_id: NonBlankString
    page_url: AnyHttpUrl
    evidence_type: NonBlankString
    observed_value: JsonValue
    claim_status: ClaimStatus
    confidence: Confidence
    captured_at: StrictAwareDatetime
    content_hash: Sha256
    evidence_urls: list[AnyHttpUrl] = Field(default_factory=list)
    confidence_gap: NonBlankString | None = None
    source_owner: NonBlankString | None = None
    rights_status: NonBlankString | None = None

    @model_validator(mode="after")
    def require_support_for_non_observed_claim(self) -> Self:
        """Require either a source URL or an explicit gap for derived claims."""

        if (
            self.claim_status is not ClaimStatus.OBSERVED
            and not self.evidence_urls
            and self.confidence_gap is None
        ):
            raise ValueError(
                "non-observed evidence requires an HTTP(S) evidence URL or confidence_gap"
            )
        return self


class AuditRecord(StrictModel):
    """Summary of one candidate audit execution."""

    audit_id: NonBlankString
    candidate_id: NonBlankString
    source_run_id: NonBlankString
    audit_version: NonBlankString
    status: NonBlankString
    created_at: StrictAwareDatetime
    completed_at: StrictAwareDatetime | None = None
    evidence_ids: list[NonBlankString] = Field(default_factory=list)
    score_names: list[NonBlankString] = Field(default_factory=list)
    issue_ids: list[NonBlankString] = Field(default_factory=list)
    error: NonBlankString | None = None


class ScoreRecord(StrictModel):
    """A rubric score with an auditable evidence trail."""

    score_name: NonBlankString
    score_value: float = Field(strict=True, allow_inf_nan=False, ge=0, le=100)
    rubric_version: NonBlankString
    inputs: dict[str, JsonValue]
    evidence_ids: list[NonBlankString]
    deterministic: StrictBool
    explanation_vi: NonBlankString
    matched_rules: list[NonBlankString] = Field(default_factory=list)
    unavailable_inputs: list[NonBlankString] = Field(default_factory=list)
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def require_evidence_for_deterministic_score(self) -> Self:
        """Prevent deterministic results without traceable evidence."""

        if self.deterministic and not self.evidence_ids:
            raise ValueError("deterministic scores require at least one evidence ID")
        return self


class IssueRecord(StrictModel):
    """An actionable audit issue supported by evidence."""

    issue_id: NonBlankString
    candidate_id: NonBlankString
    title: NonBlankString
    severity: Severity
    evidence_ids: list[NonBlankString] = Field(min_length=1)
    recommendation_vi: NonBlankString
    description: NonBlankString | None = None
    page_url: AnyHttpUrl | None = None


class ArtifactEnvelope(StrictModel):
    """Versioned, attributable wrapper around a canonical JSON payload."""

    schema_version: NonBlankString
    generator_version: NonBlankString
    source_run_id: NonBlankString
    created_at: StrictAwareDatetime
    content_hash: Sha256
    payload: JsonValue

    @field_validator("created_at")
    @classmethod
    def require_utc_created_at(cls, value: datetime) -> datetime:
        """Keep artifact timestamps unambiguous and canonical."""

        if value.utcoffset() != timedelta(0):
            raise ValueError("artifact created_at must be UTC")
        return value


class FeedbackEvent(StrictModel):
    """A bot callback or reviewer decision applied to a project."""

    event_id: NonBlankString
    event_type: NonBlankString
    project_id: NonBlankString
    actor_id: NonBlankString
    action: NonBlankString
    created_at: StrictAwareDatetime
    component_set_id: NonBlankString | None = None
    page_id: NonBlankString | None = None
    project_state: ProjectState | None = None
    page_state: PageState | None = None
    state_version: StrictNonNegativeInt | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class DeliveryRecord(StrictModel):
    """Idempotent outbound delivery attempt."""

    delivery_id: NonBlankString
    event_type: NonBlankString
    project_id: NonBlankString
    channel_id: NonBlankString
    payload_path: NonBlankString
    idempotency_key: NonBlankString
    status: DeliveryState
    attempt_count: StrictNonNegativeInt = 0
    last_error: NonBlankString | None = None
    message_id: NonBlankString | None = None
    message_url: AnyHttpUrl | None = None


class ComponentSet(StrictModel):
    """Bot-owned interactive component state used to validate callbacks."""

    component_set_id: NonBlankString
    message_id: NonBlankString
    channel_id: NonBlankString
    project_id: NonBlankString
    card_type: NonBlankString
    allowed_actions: list[NonBlankString] = Field(min_length=1)
    expires_at: StrictAwareDatetime
    state_version: StrictNonNegativeInt
    project_state: ProjectState
    page_state: PageState | None = None
    page_id: NonBlankString | None = None


class StageRecord(StrictModel):
    """Resumable status for one orchestration stage."""

    stage_id: NonBlankString
    run_id: NonBlankString
    stage_name: NonBlankString
    status: NonBlankString
    attempt_count: StrictNonNegativeInt = 0
    started_at: StrictAwareDatetime | None = None
    completed_at: StrictAwareDatetime | None = None
    error: NonBlankString | None = None
    checkpoint: dict[str, JsonValue] = Field(default_factory=dict)


class RunRecord(StrictModel):
    """Top-level resumable orchestration run."""

    run_id: NonBlankString
    status: NonBlankString
    started_at: StrictAwareDatetime
    idempotency_key: NonBlankString | None = None
    config_version: NonBlankString | None = None
    completed_at: StrictAwareDatetime | None = None
    current_stage: NonBlankString | None = None
    error: NonBlankString | None = None
    stages: list[StageRecord] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


def export_schemas(output_dir: Path) -> None:
    """Export canonical schemas through the public models API used by the runbook."""

    # Delayed import avoids a module cycle because artifact I/O consumes these models.
    from openclaw_web.artifacts import export_schemas as export_artifact_schemas

    export_artifact_schemas(output_dir)
