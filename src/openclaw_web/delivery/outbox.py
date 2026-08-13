"""Idempotent delivery dispatcher backed by immutable delivery snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import ValidationError

from openclaw_web.db.repository import Repository, RepositoryConflict
from openclaw_web.models import ComponentSet, DeliveryRecord, DeliveryState

from .openclaw_transport import SentMessage


class DeliveryTransport(Protocol):
    def send(self, delivery: DeliveryRecord) -> SentMessage: ...


class OutboxWorker:
    def __init__(self, repository: Repository, transport: DeliveryTransport) -> None:
        self.repository, self.transport = repository, transport

    def get(self, delivery_id: str) -> DeliveryRecord:
        return self.repository.get_delivery(delivery_id)

    def _transition(
        self,
        record: DeliveryRecord,
        replacement: DeliveryRecord,
    ) -> DeliveryRecord:
        return self.repository.transition_delivery(record.delivery_id, record, replacement)

    @staticmethod
    def _component_metadata(record: DeliveryRecord) -> dict[str, object] | None:
        if record.event_type not in {"review-card", "page-card", "final-card"}:
            return None
        path = Path(record.payload_path)
        if not path.is_file() or path.stat().st_size > 131_072:
            raise ValueError("interactive delivery payload is unavailable or too large")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("interactive delivery payload is not valid UTF-8 JSON") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("component_set"), dict):
            raise TypeError("interactive delivery payload is missing component metadata")
        metadata = dict(payload["component_set"])
        if "channel_id" in metadata or "message_id" in metadata:
            raise ValueError("bot message identity must come from the transport")
        return metadata

    @staticmethod
    def _validated_message_url(record: DeliveryRecord, sent: SentMessage) -> str:
        parts = urlsplit(sent.message_url)
        path = tuple(item for item in parts.path.split("/") if item)
        if (
            parts.scheme != "https"
            or (parts.hostname or "").casefold() != "discord.com"
            or len(path) != 4
            or path[0] != "channels"
            or path[2] != record.channel_id
            or path[3] != sent.message_id
        ):
            raise ValueError("transport returned an invalid Discord message identity")
        return sent.message_url

    def _persist_component(
        self,
        record: DeliveryRecord,
        metadata: dict[str, object] | None,
    ) -> None:
        if metadata is None:
            return
        if record.message_id is None:
            raise ValueError("bot message identity is missing")
        component = ComponentSet.model_validate_json(
            json.dumps(
                {
                    **metadata,
                    "channel_id": record.channel_id,
                    "message_id": record.message_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        if component.project_id != record.project_id:
            raise ValueError("component project does not match delivery project")
        project = self.repository.connection.execute(
            "SELECT state, state_version FROM projects WHERE project_id = ?",
            (record.project_id,),
        ).fetchone()
        if project is None:
            raise ValueError("component project does not exist")
        if (
            int(project["state_version"]) != component.state_version
            or str(project["state"]) != component.project_state.value
        ):
            raise ValueError("component state does not match persisted project state")
        self.repository.insert_component_set(component)

    def _reconcile_sent_identity(self, record: DeliveryRecord) -> DeliveryRecord:
        persisted = self.get(record.delivery_id)
        if persisted != record:
            record = persisted
        try:
            sent = SentMessage(str(record.message_id), str(record.message_url))
            self._validated_message_url(record, sent)
        except ValueError as error:
            return self._transition(
                record,
                record.validated_replace(
                    status=DeliveryState.FAILED,
                    attempt_count=2,
                    last_error=f"stored Discord identity is invalid: {error}"[:500],
                ),
            )
        metadata = self._component_metadata(record)
        try:
            self._persist_component(record, metadata)
        except (
            OSError,
            RepositoryConflict,
            TypeError,
            UnicodeError,
            ValueError,
            ValidationError,
        ) as error:
            return self._transition(
                record,
                record.validated_replace(
                    status=DeliveryState.SENDING,
                    last_error=f"component persistence interrupted: {error}"[:500],
                )
            )
        return self._transition(
            record,
            record.validated_replace(status=DeliveryState.SENT, last_error=None),
        )

    def dispatch_once(self) -> DeliveryRecord | None:
        row = self.repository.connection.execute(
            "SELECT delivery_id FROM deliveries "
            "WHERE status = 'sending' "
            "AND json_extract(snapshot_json, '$.message_id') IS NOT NULL "
            "AND json_extract(snapshot_json, '$.message_url') IS NOT NULL "
            "ORDER BY rowid LIMIT 1"
        ).fetchone()
        if row is not None:
            record = self.get(str(row["delivery_id"]))
            if record.message_id is not None and record.message_url is not None:
                return self._reconcile_sent_identity(record)
        claimed = self.repository.claim_next_delivery(max_attempts=2)
        if claimed is None:
            return None
        record = claimed
        try:
            metadata = self._component_metadata(record)
        except (OSError, UnicodeError, TypeError, ValueError) as error:
            return self._transition(
                record,
                record.validated_replace(
                    status=DeliveryState.FAILED,
                    attempt_count=record.attempt_count + 1,
                    last_error=str(error)[:500],
                )
            )
        try:
            sent = self.transport.send(record)
        except Exception as error:  # noqa: BLE001 - transport boundary preserves failures for retry
            return self._transition(record, record.validated_replace(status=DeliveryState.FAILED, attempt_count=record.attempt_count + 1, last_error=str(error)[:500]))
        if not sent.message_id or not sent.message_url:
            return self._transition(record, record.validated_replace(status=DeliveryState.FAILED, attempt_count=record.attempt_count + 1, last_error="missing bot message identity"))
        try:
            message_url = self._validated_message_url(record, sent)
        except ValueError as error:
            return self._transition(
                record,
                record.validated_replace(
                    status=DeliveryState.SENDING,
                    attempt_count=record.attempt_count + 1,
                    message_id=sent.message_id,
                    message_url=sent.message_url,
                    last_error=str(error),
                )
            )
        identified = self._transition(
            record,
            record.validated_replace(
                attempt_count=record.attempt_count + 1,
                message_id=sent.message_id,
                message_url=message_url,
                last_error="component persistence interrupted",
            )
        )
        try:
            self._persist_component(identified, metadata)
        except (
            OSError,
            RepositoryConflict,
            TypeError,
            UnicodeError,
            ValueError,
            ValidationError,
        ) as error:
            return self._transition(
                identified,
                identified.validated_replace(
                    last_error=f"component persistence interrupted: {error}"[:500]
                )
            )
        try:
            return self._transition(
                identified,
                identified.validated_replace(status=DeliveryState.SENT, last_error=None),
            )
        except RepositoryConflict:
            return self.get(identified.delivery_id)
