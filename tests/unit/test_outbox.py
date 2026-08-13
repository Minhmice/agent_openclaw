from __future__ import annotations

from pathlib import Path

from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository
from openclaw_web.delivery.outbox import OutboxWorker, SentMessage
from openclaw_web.models import DeliveryRecord, DeliveryState


class FakeTransport:
    def __init__(self) -> None:
        self.call_count = 0
        self.send_result = SentMessage("message-1", "https://discord.com/channels/g/c/message-1")

    def send(self, delivery: DeliveryRecord) -> SentMessage:
        self.call_count += 1
        return self.send_result


def _delivery(payload: Path) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id="delivery-1", event_type="review-card", project_id="project-1",
        channel_id="c", payload_path=str(payload), idempotency_key="review:project-1",
        status=DeliveryState.PENDING,
    )


def test_delivery_retry_does_not_duplicate_sent_message(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 0, '{}')")
    repository = Repository(db)
    record = repository.enqueue_delivery(_delivery(tmp_path / "payload.json"))
    transport = FakeTransport()
    worker = OutboxWorker(repository, transport)

    worker.dispatch_once()
    worker.dispatch_once()

    assert transport.call_count == 1
    assert worker.get(record.delivery_id).status is DeliveryState.SENT


def test_delivery_failure_retries_once_then_remains_failed(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 0, '{}')")
    repository = Repository(db)
    repository.enqueue_delivery(_delivery(tmp_path / "payload.json"))

    class FailingTransport:
        def send(self, delivery: DeliveryRecord) -> SentMessage:
            raise RuntimeError("unavailable")

    worker = OutboxWorker(repository, FailingTransport())
    worker.dispatch_once()
    worker.dispatch_once()
    worker.dispatch_once()

    result = worker.get("delivery-1")
    assert result.status is DeliveryState.FAILED
    assert result.attempt_count == 2
