from __future__ import annotations

from datetime import UTC, datetime

from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository
from openclaw_web.feedback.calibration import build_calibration_report
from openclaw_web.models import FeedbackEvent


def _feedback(project_id: str, action: str, reason: str | None, score: int) -> FeedbackEvent:
    payload = {"score_snapshot": score, "cohort": "hanoi"}
    if reason is not None:
        payload["reason_code"] = reason
    return FeedbackEvent(
        event_id=f"event-{project_id}", event_type="review-decision", project_id=project_id,
        actor_id="reviewer-1", action=action, created_at=datetime(2026, 8, 13, tzinfo=UTC),
        payload=payload,
    )


def test_calibration_report_groups_reject_reasons_without_mutating_rubric(tmp_path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    repository = Repository(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    for project_id in ("p1", "p2"):
        db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES (?, 'c1', 'review', 0, '{}')", (project_id,))
    repository.append_feedback(_feedback("p1", "reject", "business-too-weak", 82))
    repository.append_feedback(_feedback("p2", "approve", None, 78))

    report = build_calibration_report(repository)

    assert report.approve_rate == 0.5
    assert report.reject_reasons == {"business-too-weak": 1}
    assert report.score_distribution == {"70-79": 1, "80-89": 1}
    assert report.threshold_simulation[75]["qualified"] == 2
    repository.close()
