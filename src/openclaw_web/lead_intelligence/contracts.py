"""Contracts and models for Lead Intelligence.

StageOutcome in this module is the canonical Pydantic model for resumable multi-stage
intelligence runs persisted via LeadStore. It is distinct from
openclaw_web.pipeline.stages.ArtifactStageOutcome, which models filesystem checkpoints
in audit workflows.
"""

from openclaw_web.lead_contracts import (
    ALLOWLISTED_SCREENSHOT_ASSETS,
    GATE_THRESHOLDS,
    SCHEMA_VERSION,
    GateResult,
    LeadAssessment,
    LeadScores,
    PortfolioDecision,
    PortfolioEntry,
    PortfolioEntryState,
    RedTeamVerdict,
    StageOutcome,
    StageStatus,
    choose_portfolio,
    evaluate_gates,
    export_lead_schemas,
    select_defensible_survivors,
    validate_pre_outreach_payload,
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
