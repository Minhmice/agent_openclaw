from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository
from openclaw_web.delivery.outbox import OutboxWorker, SentMessage
from openclaw_web.models import DeliveryRecord, DeliveryState, ProjectState


class FakeTransport:
    def __init__(self) -> None:
        self.call_count = 0
        self.send_result = SentMessage("message-1", "https://discord.com/channels/g/c/message-1")

    def send(self, delivery: DeliveryRecord) -> SentMessage:
        self.call_count += 1
        return self.send_result


def _delivery(
    payload: Path,
    *,
    delivery_id: str = "delivery-1",
    idempotency_key: str = "review:project-1",
) -> DeliveryRecord:
    if not payload.exists():
        payload.write_text(
            json.dumps(
                {
                    "component_set": {
                        "component_set_id": "component-project-1-v0",
                        "project_id": "project-1",
                        "card_type": "review",
                        "allowed_actions": ["approve"],
                        "expires_at": datetime(2099, 1, 1, tzinfo=UTC).isoformat(),
                        "state_version": 0,
                        "project_state": ProjectState.REVIEW.value,
                    },
                    "components": {"reusable": True, "blocks": []},
                }
            ),
            encoding="utf-8",
        )
    return DeliveryRecord(
        delivery_id=delivery_id, event_type="review-card", project_id="project-1",
        channel_id="c", payload_path=str(payload), idempotency_key=idempotency_key,
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
    repository.close()


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
    repository.close()


def test_sent_review_card_persists_bot_owned_component_identity(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 4, '{}')")
    repository = Repository(db)
    payload = tmp_path / "review-card.json"
    payload.write_text(
        json.dumps(
            {
                "component_set": {
                    "component_set_id": "component-project-1-v4",
                    "project_id": "project-1",
                    "card_type": "review",
                    "allowed_actions": ["approve", "reject", "request-changes"],
                    "expires_at": datetime(2026, 8, 14, tzinfo=UTC).isoformat(),
                    "state_version": 4,
                    "project_state": ProjectState.REVIEW.value,
                },
                "components": {"reusable": True, "blocks": []},
            }
        ),
        encoding="utf-8",
    )
    repository.enqueue_delivery(_delivery(payload))

    result = OutboxWorker(repository, FakeTransport()).dispatch_once()

    assert result is not None and result.status is DeliveryState.SENT
    component = repository.get_component_set("c", "message-1")
    assert component is not None
    assert component.component_set_id == "component-project-1-v4"
    assert component.project_id == "project-1"
    assert component.state_version == 4
    repository.close()


def test_delivery_reconciles_component_state_without_resending(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 0, '{}')")
    repository = Repository(db)
    record = repository.enqueue_delivery(_delivery(tmp_path / "payload.json"))
    sent_identity = record.validated_replace(
        status=DeliveryState.SENDING,
        attempt_count=1,
        message_id="message-1",
        message_url="https://discord.com/channels/g/c/message-1",
        last_error="component persistence interrupted",
    )
    db.execute(
        "UPDATE deliveries SET status = ?, snapshot_json = ? WHERE delivery_id = ?",
        (sent_identity.status.value, sent_identity.model_dump_json(), sent_identity.delivery_id),
    )
    transport = FakeTransport()

    result = OutboxWorker(repository, transport).dispatch_once()

    assert result is not None and result.status is DeliveryState.SENT
    assert transport.call_count == 0
    assert repository.get_component_set("c", "message-1") is not None
    repository.close()


def test_exhausted_failure_does_not_block_later_pending_delivery(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 0, '{}')")
    repository = Repository(db)
    first = repository.enqueue_delivery(_delivery(tmp_path / "first.json"))
    repository.transition_delivery(
        first.delivery_id,
        first,
        first.validated_replace(
            status=DeliveryState.FAILED,
            attempt_count=2,
            last_error="exhausted",
        ),
    )
    second = repository.enqueue_delivery(
        _delivery(
            tmp_path / "second.json",
            delivery_id="delivery-2",
            idempotency_key="review:project-1:v2",
        )
    )
    transport = FakeTransport()

    result = OutboxWorker(repository, transport).dispatch_once()

    assert result is not None and result.delivery_id == second.delivery_id
    assert result.status is DeliveryState.SENT
    assert transport.call_count == 1
    assert repository.get_delivery(first.delivery_id).status is DeliveryState.FAILED
    repository.close()


def test_invalid_stored_discord_identity_becomes_terminal_without_resending(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 0, '{}')")
    repository = Repository(db)
    record = repository.enqueue_delivery(_delivery(tmp_path / "payload.json"))
    sending = repository.transition_delivery(
        record.delivery_id,
        record,
        record.validated_replace(
            status=DeliveryState.SENDING,
            attempt_count=1,
            message_id="message-1",
            message_url="https://discord.com/channels/g/wrong-channel/message-1",
            last_error="component persistence interrupted",
        ),
    )
    transport = FakeTransport()

    result = OutboxWorker(repository, transport).dispatch_once()

    assert result is not None and result.status is DeliveryState.FAILED
    assert result.attempt_count == 2
    assert result == repository.get_delivery(sending.delivery_id)
    assert result.last_error is not None and "identity" in result.last_error
    assert transport.call_count == 0
    assert repository.get_component_set("c", "message-1") is None
    repository.close()


def test_reconciliation_revalidates_persisted_identity_after_initial_read(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 0, '{}')")
    repository = Repository(db)
    record = repository.enqueue_delivery(_delivery(tmp_path / "payload.json"))
    sending = repository.transition_delivery(
        record.delivery_id,
        record,
        record.validated_replace(
            status=DeliveryState.SENDING,
            attempt_count=1,
            message_id="message-1",
            message_url="https://discord.com/channels/g/c/message-1",
            last_error="component persistence interrupted",
        ),
    )
    original_get = repository.get_delivery
    reads = 0

    def change_after_first_read(delivery_id: str) -> DeliveryRecord:
        nonlocal reads
        loaded = original_get(delivery_id)
        reads += 1
        if reads == 1:
            changed = loaded.validated_replace(
                message_url="https://discord.com/channels/g/wrong/message-1"
            )
            db.execute(
                "UPDATE deliveries SET snapshot_json = ? WHERE delivery_id = ?",
                (changed.model_dump_json(), delivery_id),
            )
        return loaded

    repository.get_delivery = change_after_first_read  # type: ignore[method-assign]

    result = OutboxWorker(repository, FakeTransport()).dispatch_once()

    assert result is not None and result.status is DeliveryState.FAILED
    assert result != sending
    assert result.last_error is not None and "identity" in result.last_error
    assert repository.get_component_set("c", "message-1") is None
    repository.close()


def test_in_flight_sending_without_identity_does_not_block_next_pending(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 0, '{}')")
    repository = Repository(db)
    first = repository.enqueue_delivery(_delivery(tmp_path / "first.json"))
    in_flight = repository.transition_delivery(
        first.delivery_id,
        first,
        first.validated_replace(status=DeliveryState.SENDING),
    )
    second = repository.enqueue_delivery(
        _delivery(
            tmp_path / "second.json",
            delivery_id="delivery-2",
            idempotency_key="review:project-1:v2",
        )
    )
    transport = FakeTransport()

    result = OutboxWorker(repository, transport).dispatch_once()

    assert result is not None and result.delivery_id == second.delivery_id
    assert result.status is DeliveryState.SENT
    assert repository.get_delivery(first.delivery_id) == in_flight
    assert transport.call_count == 1
    repository.close()


def test_in_flight_sending_without_identity_does_not_block_reconciliation(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 0, '{}')")
    repository = Repository(db)
    first = repository.enqueue_delivery(_delivery(tmp_path / "first.json"))
    in_flight = repository.transition_delivery(
        first.delivery_id,
        first,
        first.validated_replace(status=DeliveryState.SENDING),
    )
    second = repository.enqueue_delivery(
        _delivery(
            tmp_path / "second.json",
            delivery_id="delivery-2",
            idempotency_key="review:project-1:v2",
        )
    )
    repository.transition_delivery(
        second.delivery_id,
        second,
        second.validated_replace(
            status=DeliveryState.SENDING,
            attempt_count=1,
            message_id="message-2",
            message_url="https://discord.com/channels/g/c/message-2",
            last_error="component persistence interrupted",
        ),
    )
    transport = FakeTransport()

    result = OutboxWorker(repository, transport).dispatch_once()

    assert result is not None and result.delivery_id == second.delivery_id
    assert result.status is DeliveryState.SENT
    assert repository.get_delivery(first.delivery_id) == in_flight
    assert transport.call_count == 0
    repository.close()


def test_missing_component_metadata_fails_delivery_without_leaving_it_claimed(
    tmp_path: Path,
) -> None:
    db = connect(tmp_path / "state.sqlite")
    migrate(db)
    db.execute("INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) VALUES ('c1', 'example.com', 'Example', 'discovered', '{}')")
    db.execute("INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) VALUES ('project-1', 'c1', 'review', 0, '{}')")
    repository = Repository(db)
    payload = tmp_path / "malformed.json"
    payload.write_text('{"components":{}}', encoding="utf-8")
    repository.enqueue_delivery(_delivery(payload))

    result = OutboxWorker(repository, FakeTransport()).dispatch_once()

    assert result is not None and result.status is DeliveryState.FAILED
    assert result.attempt_count == 1
    assert result.last_error == "interactive delivery payload is missing component metadata"
    repository.close()
