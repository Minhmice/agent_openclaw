"""Bounded, resumable execution boundaries for the lead flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from openclaw_web.lead_intelligence.contracts import StageOutcome, StageStatus
from openclaw_web.topology import CANONICAL_STAGE_FLOW

from .stages import canonical_hash

CANONICAL_LEAD_STAGES: tuple[str, ...] = CANONICAL_STAGE_FLOW


class StageExecutionError(ValueError):
    """Raised when a stage request violates the bounded execution contract."""


class StageProducer(Protocol):
    def __call__(self) -> object: ...


class LeadStageExecutor:
    """Execute local/dispatchable stages with bounded retries and checkpoints."""

    def __init__(
        self,
        *,
        run_id: str,
        max_attempts: int = 2,
        now: Callable[[], datetime] | None = None,
        initial_outcomes: tuple[StageOutcome, ...] | list[StageOutcome] = (),
        persist_outcome: Callable[[StageOutcome], object] | None = None,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-blank string")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 5
        ):
            raise ValueError("max_attempts must be between 1 and 5")
        self.run_id = run_id.strip()
        self.max_attempts = max_attempts
        self._now = now or (lambda: datetime.now(UTC))
        self._persist_outcome = persist_outcome
        self._outcomes: dict[str, StageOutcome] = {}
        self._outputs: dict[str, object] = {}
        for outcome in initial_outcomes:
            if not isinstance(outcome, StageOutcome):
                raise TypeError("initial_outcomes must contain StageOutcome values")
            if outcome.run_id != self.run_id:
                raise ValueError("initial outcome belongs to another run")
            self._outcomes[outcome.stage_id] = outcome.validated_replace(reused=False)

    def _persist(self, outcome: StageOutcome) -> None:
        if self._persist_outcome is not None:
            self._persist_outcome(outcome.validated_replace(reused=False))

    def _timestamp(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise StageExecutionError("stage clock must return an aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _validate_stage(stage_name: str) -> str:
        if stage_name not in CANONICAL_LEAD_STAGES:
            raise StageExecutionError(f"unknown stage: {stage_name}")
        return stage_name

    def run_stage(
        self,
        stage_name: str,
        input_value: object,
        *,
        producer_role: str,
        producer: StageProducer,
        input_refs: tuple[str, ...] | list[str] | None = None,
        evidence_ids: tuple[str, ...] | list[str] = (),
    ) -> StageOutcome:
        """Run one stage, reusing only a matching successful checkpoint."""

        stage = self._validate_stage(stage_name)
        if not isinstance(producer_role, str) or not producer_role.strip():
            raise StageExecutionError("producer_role must be a non-blank string")
        if not callable(producer):
            raise TypeError("producer must be callable")
        input_hash = canonical_hash(input_value)
        stage_id = f"{stage}:{input_hash[:16]}"
        existing = self._outcomes.get(stage_id)
        if existing is not None and existing.status is StageStatus.COMPLETE:
            return existing.validated_replace(reused=True)

        started = self._timestamp()
        errors: list[str] = []
        for attempt in range(1, self.max_attempts + 1):
            try:
                value = producer()
                output_hash = canonical_hash(value)
                checkpoint: dict[str, Any] = {
                    "input_hash": input_hash,
                    "output_hash": output_hash,
                    "stage_index": CANONICAL_LEAD_STAGES.index(stage),
                    "last_completed_stage": stage,
                }
                outcome = StageOutcome(
                    stage_id=stage_id,
                    run_id=self.run_id,
                    producer_role=producer_role.strip(),
                    status=StageStatus.COMPLETE,
                    attempt_count=attempt,
                    started_at=started,
                    completed_at=self._timestamp(),
                    input_refs=tuple(input_refs or (f"input:{input_hash}",)),
                    output_refs=(f"output:{output_hash}",),
                    evidence_ids=tuple(evidence_ids),
                    confidence=None,
                    checkpoint=checkpoint,
                )
                self._persist(outcome)
                self._outcomes[stage_id] = outcome
                self._outputs[stage_id] = value
                return outcome
            except Exception as error:  # noqa: BLE001 - stage failures are classified below
                errors.append(type(error).__name__)

        outcome = StageOutcome(
            stage_id=stage_id,
            run_id=self.run_id,
            producer_role=producer_role.strip(),
            status=StageStatus.FAILED_RETRYABLE,
            attempt_count=self.max_attempts,
            started_at=started,
            completed_at=self._timestamp(),
            input_refs=tuple(input_refs or (f"input:{input_hash}",)),
            output_refs=(),
            evidence_ids=tuple(evidence_ids),
            confidence=None,
            error_code="stage_execution_failed",
            checkpoint={
                "input_hash": input_hash,
                "stage_index": CANONICAL_LEAD_STAGES.index(stage),
                "provider_failures": tuple(errors),
            },
        )
        self._persist(outcome)
        self._outcomes[stage_id] = outcome
        return outcome

    def output_for(self, outcome: StageOutcome) -> object:
        """Return the in-process output associated with a completed outcome."""

        try:
            return self._outputs[outcome.stage_id]
        except KeyError as error:
            raise KeyError(f"no output is available for stage {outcome.stage_id}") from error

    def run_flow(
        self,
        inputs: Mapping[str, object],
        producers: Mapping[str, StageProducer],
        *,
        producer_roles: Mapping[str, str] | None = None,
    ) -> tuple[StageOutcome, ...]:
        """Run the supplied subset in canonical order; reject out-of-flow keys."""

        unknown = (set(inputs) | set(producers)) - set(CANONICAL_LEAD_STAGES)
        if unknown:
            raise StageExecutionError(f"unknown stage: {min(unknown)}")
        missing = set(inputs) ^ set(producers)
        if missing:
            raise StageExecutionError(f"stage input/producer mismatch: {min(missing)}")
        results: list[StageOutcome] = []
        for stage in CANONICAL_LEAD_STAGES:
            if stage not in inputs:
                continue
            role = (producer_roles or {}).get(stage, stage)
            results.append(
                self.run_stage(
                    stage,
                    inputs[stage],
                    producer_role=role,
                    producer=producers[stage],
                )
            )
        return tuple(results)


__all__ = ["CANONICAL_LEAD_STAGES", "LeadStageExecutor", "StageExecutionError", "StageProducer"]
