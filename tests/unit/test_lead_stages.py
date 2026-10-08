from datetime import UTC, datetime

import pytest

from openclaw_web.lead_contracts import StageStatus
from openclaw_web.pipeline.lead_stages import (
    CANONICAL_LEAD_STAGES,
    LeadStageExecutor,
    StageExecutionError,
)


def test_stage_executor_runs_stages_in_canonical_order() -> None:
    seen: list[str] = []
    executor = LeadStageExecutor(run_id="run-1", now=lambda: datetime.now(UTC))

    outcomes = executor.run_flow(
        {stage: {"input": stage} for stage in CANONICAL_LEAD_STAGES[:3]},
        {
            stage: (lambda stage=stage: (seen.append(stage), {"stage": stage})[1])
            for stage in CANONICAL_LEAD_STAGES[:3]
        },
    )

    assert tuple(outcome.stage_id.split(":", 1)[0] for outcome in outcomes) == CANONICAL_LEAD_STAGES[:3]
    assert seen == list(CANONICAL_LEAD_STAGES[:3])
    assert all(outcome.status is StageStatus.COMPLETE for outcome in outcomes)


def test_stage_executor_reuses_only_matching_input_checkpoint() -> None:
    calls = 0
    executor = LeadStageExecutor(run_id="run-1")

    def producer() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"ok": "yes"}

    first = executor.run_stage("business_fit", {"candidate": "one"}, producer_role="business-strength", producer=producer)
    reused = executor.run_stage("business_fit", {"candidate": "one"}, producer_role="business-strength", producer=producer)
    changed = executor.run_stage("business_fit", {"candidate": "two"}, producer_role="business-strength", producer=producer)

    assert first.reused is False
    assert reused.reused is True
    assert changed.reused is False
    assert calls == 2


def test_stage_executor_bounds_retry_and_reports_retryable_failure() -> None:
    calls = 0
    executor = LeadStageExecutor(run_id="run-1", max_attempts=2)

    def producer() -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider unavailable")

    outcome = executor.run_stage("discover", {"market": "hanoi"}, producer_role="discovery-scout", producer=producer)

    assert calls == 2
    assert outcome.status is StageStatus.FAILED_RETRYABLE
    assert outcome.error_code == "stage_execution_failed"


def test_stage_executor_does_not_accept_unknown_stage() -> None:
    executor = LeadStageExecutor(run_id="run-1")

    with pytest.raises(StageExecutionError, match="unknown stage"):
        executor.run_stage("not-a-stage", {}, producer_role="test", producer=dict)


def test_stage_executor_resumes_from_persisted_outcome_and_persists_new_work() -> None:
    persisted: list[object] = []
    calls = 0
    first_executor = LeadStageExecutor(run_id="run-resume", persist_outcome=persisted.append)

    def first_producer() -> dict[str, str]:
        return {"value": "persisted"}

    first = first_executor.run_stage(
        "business_fit",
        {"candidate": "one"},
        producer_role="business-strength",
        producer=first_producer,
    )
    assert persisted == [first]

    def resumed_producer() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"value": "new"}

    resumed = LeadStageExecutor(
        run_id="run-resume",
        initial_outcomes=(first,),
        persist_outcome=persisted.append,
    )
    reused = resumed.run_stage(
        "business_fit",
        {"candidate": "one"},
        producer_role="business-strength",
        producer=resumed_producer,
    )
    changed = resumed.run_stage(
        "business_fit",
        {"candidate": "two"},
        producer_role="business-strength",
        producer=resumed_producer,
    )

    assert reused.reused is True
    assert changed.reused is False
    assert calls == 1
    assert len(persisted) == 2
