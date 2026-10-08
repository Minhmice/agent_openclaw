from openclaw_web.lead_contracts import LeadAssessment, RedTeamVerdict, StageStatus
from openclaw_web.pipeline.portfolio import (
    PortfolioManager,
    render_portfolio_summary,
)


def _assessment(
    candidate_id: str,
    opportunity: float,
    *,
    project_id: str | None = None,
) -> LeadAssessment:
    return LeadAssessment(
        candidate_id=candidate_id,
        project_id=project_id,
        company_name=f"Company {candidate_id}",
        website_url="https://example.com",
        business_strength=80,
        agency_fit=80,
        digital_gap=80,
        commercial_opportunity=opportunity,
        dealability=70,
        evidence_confidence=0.9,
        evidence_ids=(f"evidence-{candidate_id}",),
        red_team_verdict=RedTeamVerdict.SURVIVE,
    )


def test_portfolio_manager_bounds_selection_and_waits_for_human_command() -> None:
    result = PortfolioManager().select(
        "portfolio-1",
        [_assessment(str(index), 100 - index) for index in range(8)],
    )

    assert result.status is StageStatus.COMPLETE
    assert len(result.entries) == 7
    assert all(entry.next_action == "human-approval" for entry in result.entries)
    assert all(entry.state.value == "awaiting-command" for entry in result.entries)


def test_portfolio_summary_is_one_message_with_all_entries() -> None:
    result = PortfolioManager().select(
        "portfolio-1",
        [_assessment("a", 90), _assessment("b", 80), _assessment("c", 70)],
    )

    message = render_portfolio_summary(result.entries, dashboard_url="http://127.0.0.1:18080")

    assert message.count("Company ") == 3
    assert "portfolio-1" in message
    assert "http://127.0.0.1:18080" in message


def test_portfolio_summary_uses_workflow_project_id_for_fallback_command() -> None:
    result = PortfolioManager().select(
        "portfolio-commands",
        [
            _assessment("a", 90, project_id="project-alpha"),
            _assessment("b", 80, project_id="project-beta"),
            _assessment("c", 70, project_id="project-gamma"),
        ],
    )

    message = render_portfolio_summary(result.entries, dashboard_url="http://127.0.0.1:18080")

    assert "/lead-approve project-alpha" in message
    assert "/lead-request-change project-alpha" in message
    assert "/lead-approve " + result.entries[0].entry_id not in message


def test_portfolio_summary_fails_closed_when_project_mapping_is_unverified() -> None:
    result = PortfolioManager().select(
        "portfolio-unmapped",
        [_assessment("a", 90), _assessment("b", 80), _assessment("c", 70)],
    )

    message = render_portfolio_summary(result.entries, dashboard_url="http://127.0.0.1:18080")

    assert "project mapping chưa xác minh" in message
    assert "/lead-approve " + result.entries[0].entry_id not in message
