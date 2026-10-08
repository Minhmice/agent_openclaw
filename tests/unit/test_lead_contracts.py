from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from openclaw_web.lead_contracts import (
    LeadAssessment,
    PortfolioEntry,
    PortfolioEntryState,
    RedTeamVerdict,
    StageOutcome,
    StageStatus,
    evaluate_gates,
    select_defensible_survivors,
)


def test_gate_uses_independent_and_semantics() -> None:
    result = evaluate_gates(business_strength=59, agency_fit=100, digital_gap=100)

    assert result.passed is False
    assert result.failed_dimensions == ("BusinessStrength",)


def test_pre_outreach_assessment_rejects_intent_fields() -> None:
    with pytest.raises(ValidationError, match="buyer_intent"):
        LeadAssessment(
            candidate_id="candidate-1",
            company_name="Example Co",
            website_url="https://example.com",
            business_strength=80,
            agency_fit=80,
            digital_gap=80,
            conversion_gap=70,
            ux_gap=70,
            trust_gap=70,
            commercial_opportunity=80,
            dealability=60,
            evidence_confidence=0.9,
            evidence_ids=("evidence-1",),
            buyer_intent="unknown",
        )


def test_stage_outcome_rejects_sensitive_checkpoint_data() -> None:
    with pytest.raises(ValidationError, match="checkpoint"):
        StageOutcome(
            stage_id="business_fit:1",
            run_id="run-1",
            producer_role="business-strength",
            status=StageStatus.COMPLETE,
            attempt_count=1,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            input_refs=("candidate-1",),
            output_refs=("assessment-1",),
            evidence_ids=("evidence-1",),
            confidence=0.9,
            checkpoint={"password": "must-not-persist"},
        )


def test_stage_outcome_rejects_sensitive_checkpoint_value_under_safe_key() -> None:
    with pytest.raises(ValidationError, match="checkpoint"):
        StageOutcome(
            stage_id="business_fit:1",
            run_id="run-1",
            producer_role="business-strength",
            status=StageStatus.COMPLETE,
            attempt_count=1,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            input_refs=("candidate-1",),
            output_refs=("assessment-1",),
            evidence_ids=("evidence-1",),
            confidence=0.9,
            checkpoint={"cursor": "Authorization: Bearer private-token"},
        )


def test_rejected_red_team_verdict_never_becomes_portfolio_entry() -> None:
    assessment = LeadAssessment(
        candidate_id="candidate-1",
        company_name="Example Co",
        website_url="https://example.com",
        business_strength=90,
        agency_fit=90,
        digital_gap=90,
        conversion_gap=80,
        ux_gap=80,
        trust_gap=80,
        commercial_opportunity=90,
        dealability=70,
        evidence_confidence=0.95,
        evidence_ids=("evidence-1",),
        red_team_verdict=RedTeamVerdict.REJECT,
    )

    assert select_defensible_survivors((assessment,)) == ()


def test_assessment_without_evidence_ids_is_not_defensible() -> None:
    assessment = LeadAssessment(
        candidate_id="candidate-no-evidence",
        company_name="Example Co",
        website_url="https://example.com",
        business_strength=90,
        agency_fit=90,
        digital_gap=90,
        commercial_opportunity=90,
        dealability=70,
        evidence_confidence=0.95,
        red_team_verdict=RedTeamVerdict.SURVIVE,
    )

    assert select_defensible_survivors((assessment,)) == ()


def test_portfolio_entry_state_is_explicit_and_versioned() -> None:
    entry = PortfolioEntry(
        entry_id="entry-1",
        portfolio_id="portfolio-1",
        candidate_id="candidate-1",
        rank=1,
        company_name="Example Co",
        website_url="https://example.com",
        business_strength=80,
        agency_fit=80,
        digital_gap=80,
        evidence_confidence=0.9,
        red_team_verdict=RedTeamVerdict.SURVIVE,
    )

    assert entry.state is PortfolioEntryState.RANKED
    assert entry.state_version == 0


def test_project_id_is_optional_until_a_workflow_project_is_created() -> None:
    assessment = LeadAssessment(
        candidate_id="candidate-1",
        project_id="project-1",
        company_name="Example Co",
        website_url="https://example.com",
        business_strength=80,
        agency_fit=80,
        digital_gap=80,
        commercial_opportunity=80,
        dealability=60,
        evidence_confidence=0.9,
        evidence_ids=("evidence-1",),
        red_team_verdict=RedTeamVerdict.SURVIVE,
    )

    assert assessment.project_id == "project-1"
