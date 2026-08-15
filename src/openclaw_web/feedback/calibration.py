"""Read-only feedback aggregation; it never changes the scoring rubric."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from openclaw_web.db.repository import Repository
from openclaw_web.models import FeedbackEvent

REASON_CODES = frozenset({
    "business-too-weak", "website-not-weak", "money-opportunity-weak",
    "evidence-insufficient", "wrong-location", "duplicate", "wrong-industry-fit",
    "contact-unusable", "false-positive-technical", "other",
})


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    total_events: int
    approve_rate: float
    reject_reasons: dict[str, int]
    score_distribution: dict[str, int]
    cohort_precision: dict[str, float]
    threshold_simulation: dict[int, dict[str, int]]


def _bucket(score: float) -> str:
    floor = int(score // 10) * 10
    return f"{floor}-{floor + 9}"


def _score(event: FeedbackEvent) -> float | None:
    value = event.payload.get("score_snapshot")
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def build_calibration_report(repository: Repository) -> CalibrationReport:
    """Aggregate persisted snapshots only; no scoring configuration is loaded or mutated."""
    rows = repository.connection.execute("SELECT snapshot_json FROM feedback ORDER BY created_at").fetchall()
    events = [FeedbackEvent.model_validate_json(str(row["snapshot_json"])) for row in rows]
    approvals = sum(event.action == "approve" for event in events)
    reasons: Counter[str] = Counter()
    scores: Counter[str] = Counter()
    cohort_counts: Counter[str] = Counter()
    cohort_approvals: Counter[str] = Counter()
    numeric_scores: list[float] = []
    for event in events:
        reason = event.payload.get("reason_code")
        if event.action == "reject" and isinstance(reason, str) and reason in REASON_CODES:
            reasons[reason] += 1
        score = _score(event)
        if score is not None:
            scores[_bucket(score)] += 1
            numeric_scores.append(score)
        cohort = event.payload.get("cohort")
        if isinstance(cohort, str) and cohort:
            cohort_counts[cohort] += 1
            cohort_approvals[cohort] += event.action == "approve"
    thresholds = {threshold: {"qualified": sum(score >= threshold for score in numeric_scores), "total": len(numeric_scores)} for threshold in (65, 70, 75, 80, 85)}
    return CalibrationReport(
        total_events=len(events), approve_rate=approvals / len(events) if events else 0.0,
        reject_reasons=dict(reasons), score_distribution=dict(scores),
        cohort_precision={key: cohort_approvals[key] / count for key, count in cohort_counts.items()},
        threshold_simulation=thresholds,
    )
