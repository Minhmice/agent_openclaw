from openclaw_web.lead_contracts import LeadAssessment, RedTeamVerdict, StageStatus
from openclaw_web.pipeline.lead_intelligence import LeadIntelligenceEngine


def _assessment(candidate_id: str, verdict: RedTeamVerdict = RedTeamVerdict.SURVIVE) -> LeadAssessment:
    return LeadAssessment(
        candidate_id=candidate_id,
        company_name=f"Company {candidate_id}",
        website_url="https://example.com",
        business_strength=80,
        agency_fit=80,
        digital_gap=80,
        commercial_opportunity=80,
        dealability=60,
        evidence_confidence=0.9,
        evidence_ids=(f"evidence-{candidate_id}",),
        red_team_verdict=verdict,
    )


def test_engine_returns_partial_without_fabricating_two_survivors() -> None:
    result = LeadIntelligenceEngine().build_result(
        run_id="run-1",
        assessments=(_assessment("one"), _assessment("two")),
    )

    assert result.status == StageStatus.PARTIAL.value
    assert result.portfolio_id is None
    assert result.selected_entries == ()


def test_engine_returns_no_candidate_when_all_red_team_rejected() -> None:
    result = LeadIntelligenceEngine().build_result(
        run_id="run-1",
        assessments=(_assessment("one", RedTeamVerdict.REJECT),),
    )

    assert result.status == StageStatus.NO_CANDIDATE_DEFENSIBLE.value
    assert result.portfolio_id is None
    assert result.provider_failures == ()


def test_engine_persists_one_bounded_portfolio_for_three_survivors() -> None:
    calls: list[str] = []

    class Repository:
        def create_portfolio(self, **kwargs: object) -> bool:
            calls.append(str(kwargs["portfolio_id"]))
            assert len(kwargs["entries"]) == 3  # type: ignore[arg-type]
            return True

    result = LeadIntelligenceEngine(repository=Repository()).build_result(
        run_id="run-1",
        assessments=(_assessment("one"), _assessment("two"), _assessment("three")),
    )

    assert result.status == StageStatus.COMPLETE.value
    assert result.portfolio_id is not None
    assert calls == [result.portfolio_id]


def test_engine_delivers_a_new_portfolio_only_once() -> None:
    created: set[str] = set()
    reserved: set[str] = set()
    deliveries: list[str] = []

    class Repository:
        def create_portfolio(self, **kwargs: object) -> bool:
            portfolio_id = str(kwargs["portfolio_id"])
            if portfolio_id in created:
                return False
            created.add(portfolio_id)
            return True

        def record_portfolio_delivery(self, **kwargs: object) -> bool:
            portfolio_id = str(kwargs["portfolio_id"])
            if portfolio_id in reserved:
                return False
            reserved.add(portfolio_id)
            return True

        def transition_portfolio_delivery(self, portfolio_id: str, *, expected_status: str, status: str) -> bool:
            assert portfolio_id
            assert expected_status == "pending"
            assert status == "sent"
            return True

    engine = LeadIntelligenceEngine(
        repository=Repository(),
        delivery_sink=lambda delivery: deliveries.append(delivery.portfolio_id),
    )
    assessments = (_assessment("one"), _assessment("two"), _assessment("three"))

    first = engine.build_result(run_id="same-run", assessments=assessments)
    second = engine.build_result(run_id="same-run", assessments=assessments)

    assert first.portfolio_id == second.portfolio_id
    assert deliveries == [first.portfolio_id]
