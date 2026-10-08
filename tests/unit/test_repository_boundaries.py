from datetime import UTC, datetime
from pathlib import Path

import pytest

from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository, RepositoryConflict
from openclaw_web.lead_contracts import PortfolioEntry, RedTeamVerdict, StageOutcome, StageStatus


def _entry(portfolio_id: str, *, entry_id: str = "entry-1") -> PortfolioEntry:
    return PortfolioEntry(
        entry_id=entry_id,
        portfolio_id=portfolio_id,
        candidate_id=f"candidate-{entry_id}",
        rank=1,
        company_name="Example Co",
        website_url="https://example.com",
        business_strength=80,
        agency_fit=80,
        digital_gap=80,
        evidence_confidence=0.9,
        red_team_verdict=RedTeamVerdict.SURVIVE,
    )


def test_portfolio_persistence_is_atomic_and_idempotent(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite")
    migrate(connection)
    repository = Repository(connection)
    run = repository.create_or_resume_run("lead-run-1", "lead-intelligence.v1")

    assert repository.create_portfolio(
        portfolio_id="portfolio-1",
        run_id=run.run_id,
        status="complete",
        entries=(_entry("portfolio-1"),),
    )
    assert not repository.create_portfolio(
        portfolio_id="portfolio-1",
        run_id=run.run_id,
        status="complete",
        entries=(_entry("portfolio-1"),),
    )
    assert repository.get_portfolio_entries("portfolio-1")[0].entry_id == "entry-1"

    changed = _entry("portfolio-1").validated_replace(company_name="Changed Co")
    with pytest.raises(RepositoryConflict, match="different content"):
        repository.create_portfolio(
            portfolio_id="portfolio-1",
            run_id=run.run_id,
            status="complete",
            entries=(changed,),
        )


def test_portfolio_rejects_entry_for_another_portfolio_without_partial_rows(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite")
    migrate(connection)
    repository = Repository(connection)

    with pytest.raises(ValueError, match="portfolio_id"):
        repository.create_portfolio(
            portfolio_id="portfolio-1",
            run_id=None,
            status="complete",
            entries=(_entry("portfolio-2"),),
        )

    assert connection.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 0


def test_dashboard_action_receipt_replay_returns_same_result(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite")
    migrate(connection)
    repository = Repository(connection)

    first = repository.claim_dashboard_action(
        idempotency_key="action-1",
        action="watch-lead",
        target_id="entry-1",
        actor_id="minh",
        expected_state_version=0,
    )
    assert first.status == "pending"
    repository.complete_dashboard_action(
        "action-1",
        status="succeeded",
        response={"status": "accepted", "state": "watching"},
    )

    replay = repository.claim_dashboard_action(
        idempotency_key="action-1",
        action="watch-lead",
        target_id="entry-1",
        actor_id="minh",
        expected_state_version=0,
    )
    assert replay.status == "succeeded"
    assert replay.response == {"status": "accepted", "state": "watching"}


def test_dashboard_action_receipt_rejects_reused_key_with_different_identity(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite")
    migrate(connection)
    repository = Repository(connection)
    repository.claim_dashboard_action(
        idempotency_key="action-1",
        action="watch-lead",
        target_id="entry-1",
        actor_id="minh",
        expected_state_version=0,
    )

    with pytest.raises(RepositoryConflict):
        repository.claim_dashboard_action(
            idempotency_key="action-1",
            action="lead-reject",
            target_id="entry-2",
            actor_id="minh",
            expected_state_version=0,
        )


def test_stage_outcomes_are_durable_and_replace_retryable_attempts(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite")
    migrate(connection)
    repository = Repository(connection)
    run = repository.create_or_resume_run("stage-run", "lead-intelligence.v1")
    started = datetime(2026, 8, 27, 1, 0, tzinfo=UTC)
    failed = StageOutcome(
        stage_id="business_fit:input-hash",
        run_id=run.run_id,
        producer_role="business-strength",
        status=StageStatus.FAILED_RETRYABLE,
        attempt_count=2,
        started_at=started,
        completed_at=started,
        input_refs=("input:input-hash",),
        output_refs=(),
        evidence_ids=(),
        error_code="stage_execution_failed",
        checkpoint={"input_hash": "input-hash", "stage_index": 4, "provider_failures": ("TimeoutError",)},
    )
    repository.record_stage_outcome(failed, current_stage="business_fit", run_status="running")

    complete = failed.validated_replace(
        status=StageStatus.COMPLETE,
        attempt_count=1,
        completed_at=datetime(2026, 8, 27, 1, 1, tzinfo=UTC),
        output_refs=("output:output-hash",),
        error_code=None,
        checkpoint={
            "input_hash": "input-hash",
            "output_hash": "output-hash",
            "stage_index": 4,
            "last_completed_stage": "business_fit",
        },
    )
    repository.record_stage_outcome(complete, current_stage="business_fit", run_status="running")
    repository.record_stage_outcome(complete, current_stage="business_fit", run_status="running")

    assert repository.get_stage_outcomes(run.run_id) == (complete,)
    persisted_run = repository.get_run_record(run.run_id)
    assert persisted_run is not None
    assert persisted_run.status == "running"
    assert persisted_run.current_stage == "business_fit"


def test_stage_outcome_rejects_different_content_for_completed_stage(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite")
    migrate(connection)
    repository = Repository(connection)
    run = repository.create_or_resume_run("stage-conflict", "lead-intelligence.v1")
    outcome = StageOutcome(
        stage_id="rank:stable-hash",
        run_id=run.run_id,
        producer_role="lead-ranker",
        status=StageStatus.COMPLETE,
        attempt_count=1,
        started_at=datetime(2026, 8, 27, tzinfo=UTC),
        completed_at=datetime(2026, 8, 27, 0, 1, tzinfo=UTC),
        input_refs=("input:stable-hash",),
        output_refs=("output:first",),
        evidence_ids=(),
        checkpoint={"input_hash": "stable-hash", "output_hash": "first", "stage_index": 13},
    )
    repository.record_stage_outcome(outcome, current_stage="rank", run_status="running")

    with pytest.raises(RepositoryConflict, match="completed"):
        repository.record_stage_outcome(
            outcome.validated_replace(output_refs=("output:second",)),
            current_stage="rank",
            run_status="running",
        )


def test_portfolio_delivery_reservation_and_transition_are_idempotent(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite")
    migrate(connection)
    repository = Repository(connection)
    repository.create_portfolio(
        portfolio_id="portfolio-delivery",
        run_id=None,
        status="complete",
        entries=(_entry("portfolio-delivery"),),
    )

    payload = {"message": "one summary", "created_at": "2026-08-27T00:00:00Z"}
    assert repository.record_portfolio_delivery(
        portfolio_id="portfolio-delivery",
        delivery_id="delivery-1",
        idempotency_key="portfolio:portfolio-delivery",
        status="pending",
        snapshot=payload,
    )
    assert not repository.record_portfolio_delivery(
        portfolio_id="portfolio-delivery",
        delivery_id="delivery-1",
        idempotency_key="portfolio:portfolio-delivery",
        status="pending",
        snapshot={**payload, "created_at": "2026-08-27T00:01:00Z"},
    )
    assert repository.transition_portfolio_delivery(
        "portfolio-delivery", expected_status="pending", status="sent"
    )
    assert repository.transition_portfolio_delivery(
        "portfolio-delivery", expected_status="pending", status="sent"
    )
    delivery = repository.get_portfolio_delivery("portfolio-delivery")
    assert delivery is not None
    assert delivery["status"] == "sent"
    assert delivery["snapshot"]["message"] == "one summary"

    with pytest.raises(RepositoryConflict, match="different content"):
        repository.record_portfolio_delivery(
            portfolio_id="portfolio-delivery",
            delivery_id="delivery-other",
            idempotency_key="portfolio:portfolio-delivery",
            status="pending",
            snapshot={"message": "different"},
        )
