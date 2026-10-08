"""Lead Intelligence orchestration façade."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from openclaw_web.lead_intelligence.contracts import LeadAssessment, StageOutcome, StageStatus

from .lead_stages import LeadStageExecutor, StageProducer
from .portfolio import PortfolioDelivery, PortfolioManager


@dataclass(frozen=True, slots=True)
class LeadIntelligenceRunResult:
    status: str
    run_id: str
    portfolio_id: str | None = None
    selected_entries: tuple[str, ...] = ()
    evaluated_candidates: int = 0
    provider_failures: tuple[str, ...] = ()


class LeadPortfolioRepository(Protocol):
    def create_portfolio(self, **kwargs: object) -> bool: ...


class LeadStageRepository(Protocol):
    def get_stage_outcomes(self, run_id: str) -> tuple[StageOutcome, ...]: ...

    def record_stage_outcome(
        self,
        outcome: StageOutcome,
        *,
        current_stage: str | None = None,
        run_status: str | None = None,
    ) -> object: ...


class PortfolioDeliverySink(Protocol):
    def __call__(self, delivery: PortfolioDelivery) -> object: ...


class LeadIntelligenceEngine:
    """Coordinate typed assessments and portfolio persistence.

    Specialist judgment is supplied as structured ``LeadAssessment`` values;
    this class only applies deterministic policy and persistence boundaries.
    """

    def __init__(
        self,
        *,
        repository: LeadPortfolioRepository | None = None,
        delivery_sink: PortfolioDeliverySink | None = None,
        dashboard_url: str = "http://127.0.0.1:18080",
        target: int = 5,
        maximum: int = 7,
    ) -> None:
        self._repository = repository
        self._delivery_sink = delivery_sink
        self._dashboard_url = dashboard_url
        self._portfolio_manager = PortfolioManager(target=target, maximum=maximum)

    def build_result(
        self,
        *,
        run_id: str,
        assessments: tuple[LeadAssessment, ...] | list[LeadAssessment],
        provider_failures: tuple[str, ...] | list[str] = (),
    ) -> LeadIntelligenceRunResult:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-blank string")
        values = tuple(assessments)
        if any(not isinstance(value, LeadAssessment) for value in values):
            raise TypeError("assessments must contain LeadAssessment values")
        failures = tuple(str(item) for item in provider_failures)
        selection = self._portfolio_manager.select(self._portfolio_id(run_id), values)
        if selection.status is StageStatus.NO_CANDIDATE_DEFENSIBLE:
            return LeadIntelligenceRunResult(
                status=selection.status.value,
                run_id=run_id,
                evaluated_candidates=len(values),
                provider_failures=failures,
            )
        if selection.status is StageStatus.PARTIAL and len(selection.entries) < 3:
            # Defensible one/two-lead outputs are intentionally not promoted to
            # a portfolio, so the next run cannot treat them as a bounded set.
            return LeadIntelligenceRunResult(
                status=StageStatus.PARTIAL.value,
                run_id=run_id,
                evaluated_candidates=len(values),
                provider_failures=failures,
            )

        persisted = False
        if self._repository is not None:
            persisted = self._portfolio_manager.persist(
                self._repository,
                selection,
                run_id=run_id,
            )
            if persisted and self._delivery_sink is not None:
                delivery = self._portfolio_manager.delivery(
                    selection,
                    dashboard_url=self._dashboard_url,
                )
                should_deliver = True
                record_delivery = getattr(self._repository, "record_portfolio_delivery", None)
                if callable(record_delivery):
                    should_deliver = cast(Callable[..., bool], record_delivery)(
                        portfolio_id=delivery.portfolio_id,
                        delivery_id=f"delivery-{uuid.uuid5(uuid.NAMESPACE_URL, delivery.portfolio_id).hex}",
                        idempotency_key=delivery.idempotency_key,
                        status="pending",
                        snapshot={
                            "message": delivery.message,
                            "created_at": delivery.created_at.isoformat(),
                        },
                    )
                if should_deliver:
                    try:
                        self._delivery_sink(delivery)
                    except Exception:
                        transition = getattr(
                            self._repository, "transition_portfolio_delivery", None
                        )
                        if callable(transition):
                            cast(Callable[..., object], transition)(
                                delivery.portfolio_id,
                                expected_status="pending",
                                status="failed",
                            )
                        raise
                    transition = getattr(self._repository, "transition_portfolio_delivery", None)
                    if callable(transition):
                        cast(Callable[..., object], transition)(
                            delivery.portfolio_id,
                            expected_status="pending",
                            status="sent",
                        )
        status = StageStatus.PARTIAL.value if failures else selection.status.value
        return LeadIntelligenceRunResult(
            status=status,
            run_id=run_id,
            portfolio_id=selection.portfolio_id
            if persisted or self._repository is not None
            else None,
            selected_entries=tuple(entry.entry_id for entry in selection.entries),
            evaluated_candidates=len(values),
            provider_failures=failures,
        )

    @staticmethod
    def _portfolio_id(run_id: str) -> str:
        return f"portfolio-{uuid.uuid5(uuid.NAMESPACE_URL, run_id).hex}"

    def execute_stages(
        self,
        *,
        run_id: str,
        inputs: Mapping[str, object],
        producers: Mapping[str, StageProducer],
        producer_roles: Mapping[str, str] | None = None,
    ) -> tuple[StageOutcome, ...]:
        """Expose the bounded stage executor without leaking provider loops."""

        initial_outcomes: tuple[StageOutcome, ...] = ()
        persist_outcome: Callable[[StageOutcome], object] | None = None
        if self._repository is not None:
            get_outcomes = getattr(self._repository, "get_stage_outcomes", None)
            record_outcome = getattr(self._repository, "record_stage_outcome", None)
            if callable(get_outcomes):
                initial_outcomes = cast(Callable[[str], tuple[StageOutcome, ...]], get_outcomes)(
                    run_id
                )
            if callable(record_outcome):
                callback = cast(Callable[..., object], record_outcome)

                def persist(value: StageOutcome) -> object:
                    stage_name = value.stage_id.split(":", 1)[0]
                    return callback(value, current_stage=stage_name, run_status="running")

                persist_outcome = persist
        executor = LeadStageExecutor(
            run_id=run_id,
            initial_outcomes=initial_outcomes,
            persist_outcome=persist_outcome,
        )
        return executor.run_flow(inputs, producers, producer_roles=producer_roles)


__all__ = ["LeadIntelligenceEngine", "LeadIntelligenceRunResult"]
