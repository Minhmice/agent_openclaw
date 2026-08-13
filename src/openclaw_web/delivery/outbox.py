"""Idempotent delivery dispatcher backed by immutable delivery snapshots."""

from __future__ import annotations

from typing import Protocol

from openclaw_web.db.repository import Repository
from openclaw_web.models import DeliveryRecord, DeliveryState

from .openclaw_transport import SentMessage


class DeliveryTransport(Protocol):
    def send(self, delivery: DeliveryRecord) -> SentMessage: ...


class OutboxWorker:
    def __init__(self, repository: Repository, transport: DeliveryTransport) -> None:
        self.repository, self.transport = repository, transport

    def get(self, delivery_id: str) -> DeliveryRecord:
        row = self.repository.connection.execute("SELECT snapshot_json FROM deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        return DeliveryRecord.model_validate_json(str(row["snapshot_json"]))

    def _save(self, record: DeliveryRecord) -> DeliveryRecord:
        snapshot = record.model_dump_json()
        self.repository.connection.execute("UPDATE deliveries SET status = ?, snapshot_json = ? WHERE delivery_id = ?", (record.status.value, snapshot, record.delivery_id))
        return record

    def dispatch_once(self) -> DeliveryRecord | None:
        row = self.repository.connection.execute("SELECT delivery_id FROM deliveries WHERE status IN ('pending', 'failed') ORDER BY rowid LIMIT 1").fetchone()
        if row is None:
            return None
        record = self.get(str(row["delivery_id"]))
        if record.status is DeliveryState.FAILED and record.attempt_count >= 2:
            return None
        sending = record.validated_replace(status=DeliveryState.SENDING)
        self._save(sending)
        try:
            sent = self.transport.send(sending)
        except Exception as error:  # noqa: BLE001 - transport boundary preserves failures for retry
            return self._save(sending.validated_replace(status=DeliveryState.FAILED, attempt_count=sending.attempt_count + 1, last_error=str(error)[:500]))
        if not sent.message_id or not sent.message_url:
            return self._save(sending.validated_replace(status=DeliveryState.FAILED, attempt_count=sending.attempt_count + 1, last_error="missing bot message identity"))
        return self._save(sending.validated_replace(status=DeliveryState.SENT, attempt_count=sending.attempt_count + 1, message_id=sent.message_id, message_url=sent.message_url, last_error=None))
