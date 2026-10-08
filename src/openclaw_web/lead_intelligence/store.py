"""Transactional lead intelligence store for runs, stage checkpoints, and portfolios."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from openclaw_web.platform.base import (
    BaseStore,
    _canonical_json,
    _canonical_mapping_json,
    _deserialize,
    _immediate_transaction,
    _lease_expiry,
    _require_nonblank,
    _utc_text,
)
from openclaw_web.lead_intelligence.contracts import PortfolioEntry, StageOutcome, StageStatus
from openclaw_web.models import RunRecord
from openclaw_web.platform.errors import (
    RepositoryConflict,
    RepositoryError,
    RunConfigMismatchError,
)


@dataclass(frozen=True, slots=True)
class DashboardActionReceipt:
    """Durable dashboard action claim/result used for replay protection."""

    idempotency_key: str
    action: str
    target_id: str
    actor_id: str
    expected_state_version: int
    status: str
    event_id: str
    response: dict[str, Any] | None
    error_code: str | None


class LeadStore(BaseStore):
    """Lead intelligence persistence for runs, portfolios, and dashboard action receipts."""

    def create_or_resume_run(self, idempotency_key: str, config_version: str) -> RunRecord:
        key = _require_nonblank(idempotency_key, "idempotency_key")
        version = _require_nonblank(config_version, "config_version")
        run = RunRecord(
            run_id=f"run-{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}",
            status="pending",
            started_at=datetime.now(UTC),
            idempotency_key=key,
            config_version=version,
        )
        snapshot = _canonical_json(run)

        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT snapshot_json FROM runs WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is not None:
                persisted = _deserialize(RunRecord, str(row["snapshot_json"]))
                if persisted.config_version != version:
                    raise RunConfigMismatchError(
                        "run idempotency key already exists with a different config version"
                    )
                return persisted
            self.connection.execute(
                """
                INSERT INTO runs (
                    run_id, idempotency_key, config_version, status, started_at,
                    completed_at, current_stage, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    key,
                    version,
                    run.status,
                    _utc_text(run.started_at),
                    None,
                    None,
                    snapshot,
                ),
            )
        return run

    def get_run_record(self, run_id: str) -> RunRecord | None:
        """Load one canonical run without exposing its raw database snapshot."""

        identity = _require_nonblank(run_id, "run_id")
        row = self.connection.execute(
            "SELECT snapshot_json FROM runs WHERE run_id = ?", (identity,)
        ).fetchone()
        return None if row is None else _deserialize(RunRecord, str(row["snapshot_json"]))

    @staticmethod
    def _lead_stage_outcomes(run: RunRecord) -> tuple[StageOutcome, ...]:
        payload = run.model_dump(mode="json")
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise RepositoryError("run metadata must be a JSON object")
        raw_outcomes = metadata.get("lead_stage_outcomes", [])
        if not isinstance(raw_outcomes, list):
            raise RepositoryError("lead_stage_outcomes must be a JSON array")
        outcomes: list[StageOutcome] = []
        for raw in raw_outcomes:
            if not isinstance(raw, dict):
                raise RepositoryError("lead stage outcome must be a JSON object")
            try:
                outcome = StageOutcome.model_validate_json(
                    json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise RepositoryError("lead stage outcome is invalid") from error
            if outcome.run_id != run.run_id:
                raise RepositoryError("lead stage outcome belongs to another run")
            outcomes.append(outcome)
        return tuple(outcomes)

    def get_stage_outcomes(self, run_id: str) -> tuple[StageOutcome, ...]:
        """Return the typed lead-stage checkpoint set stored in a run snapshot."""

        run = self.get_run_record(run_id)
        return () if run is None else self._lead_stage_outcomes(run)

    def record_stage_outcome(
        self,
        outcome: StageOutcome,
        *,
        current_stage: str | None = None,
        run_status: str | None = None,
    ) -> None:
        """Atomically replace one retryable stage result in the run checkpoint.

        Lead-stage outcomes live under the run's typed metadata so legacy
        ``StageRecord`` consumers and the public ``ProductionPipeline`` façade
        remain unchanged. A completed outcome is immutable; a retryable outcome
        may be replaced by its later successful attempt for the same input hash.
        """

        if not isinstance(outcome, StageOutcome):
            raise TypeError("outcome must be a StageOutcome")
        stage = _require_nonblank(
            current_stage or outcome.stage_id.split(":", 1)[0], "current_stage"
        )
        requested_status = (
            None if run_status is None else _require_nonblank(run_status, "run_status")
        )
        persisted_outcome = outcome.validated_replace(reused=False)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT status, snapshot_json FROM runs WHERE run_id = ?", (outcome.run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(outcome.run_id)
            try:
                run = _deserialize(RunRecord, str(row["snapshot_json"]))
            except (TypeError, ValueError) as error:
                raise RepositoryError("run snapshot is invalid") from error
            outcomes = list(self._lead_stage_outcomes(run))
            for index, existing in enumerate(outcomes):
                if existing.stage_id != persisted_outcome.stage_id:
                    continue
                if existing == persisted_outcome:
                    break
                if existing.status is StageStatus.COMPLETE:
                    raise RepositoryConflict("completed stage outcome cannot be replaced")
                outcomes[index] = persisted_outcome
                break
            else:
                outcomes.append(persisted_outcome)

            payload = run.model_dump(mode="python")
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                raise RepositoryError("run metadata must be a JSON object")
            metadata["lead_stage_outcomes"] = [item.model_dump(mode="json") for item in outcomes]
            payload["metadata"] = metadata
            payload["current_stage"] = stage
            existing_status = str(row["status"])
            effective_status = requested_status or existing_status
            if requested_status is None and outcome.status is not StageStatus.COMPLETE:
                effective_status = outcome.status.value
            elif requested_status is None and existing_status == "pending":
                effective_status = "running"
            payload["status"] = effective_status
            try:
                updated_run = RunRecord.model_validate(payload)
            except (TypeError, ValueError) as error:
                raise RepositoryError("updated run snapshot is invalid") from error
            cursor = self.connection.execute(
                """
                UPDATE runs
                SET status = ?, current_stage = ?, snapshot_json = ?
                WHERE run_id = ? AND snapshot_json = ?
                """,
                (
                    effective_status,
                    stage,
                    _canonical_json(updated_run),
                    outcome.run_id,
                    str(row["snapshot_json"]),
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("run snapshot changed before stage outcome update")

    def acquire_run_lock(
        self,
        key: str,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        lock_key = _require_nonblank(key, "key")
        owner_token = _require_nonblank(owner, "owner")
        now_text, expiry_text = _lease_expiry(now, lease_seconds)
        with _immediate_transaction(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO run_locks (lock_key, owner, acquired_at, lease_expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lock_key) DO UPDATE SET
                    owner = excluded.owner,
                    acquired_at = excluded.acquired_at,
                    lease_expires_at = excluded.lease_expires_at
                WHERE (
                        run_locks.owner = excluded.owner
                    AND run_locks.acquired_at <= excluded.acquired_at
                    AND run_locks.lease_expires_at <= excluded.lease_expires_at
                ) OR (
                        run_locks.owner <> excluded.owner
                    AND run_locks.lease_expires_at < excluded.acquired_at
                )
                """,
                (lock_key, owner_token, now_text, expiry_text),
            )
            return cursor.rowcount == 1

    def renew_run_lock(
        self,
        key: str,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        lock_key = _require_nonblank(key, "key")
        owner_token = _require_nonblank(owner, "owner")
        now_text, expiry_text = _lease_expiry(now, lease_seconds)
        with _immediate_transaction(self.connection):
            cursor = self.connection.execute(
                """
                UPDATE run_locks
                SET acquired_at = ?, lease_expires_at = ?
                WHERE lock_key = ?
                  AND owner = ?
                  AND lease_expires_at >= ?
                  AND acquired_at <= ?
                  AND lease_expires_at <= ?
                """,
                (
                    now_text,
                    expiry_text,
                    lock_key,
                    owner_token,
                    now_text,
                    now_text,
                    expiry_text,
                ),
            )
            return cursor.rowcount == 1

    def release_run_lock(self, key: str, owner: str) -> None:
        lock_key = _require_nonblank(key, "key")
        owner_token = _require_nonblank(owner, "owner")
        with _immediate_transaction(self.connection):
            self.connection.execute(
                "DELETE FROM run_locks WHERE lock_key = ? AND owner = ?",
                (lock_key, owner_token),
            )

    @staticmethod
    def _portfolio_entry_snapshot(entry: PortfolioEntry) -> str:
        return json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def create_portfolio(
        self,
        *,
        portfolio_id: str,
        run_id: str | None,
        status: str,
        entries: tuple[PortfolioEntry, ...] | list[PortfolioEntry],
        target: int = 5,
        maximum: int = 7,
        created_at: datetime | None = None,
    ) -> bool:
        """Persist one portfolio and all entries in one transaction.

        Replaying an identical portfolio is a no-op. A same-ID portfolio with
        different immutable content is a conflict, preserving idempotency.
        """

        identity = _require_nonblank(portfolio_id, "portfolio_id")
        if run_id is not None:
            run_id = _require_nonblank(run_id, "run_id")
        state = _require_nonblank(status, "status")
        if isinstance(target, bool) or not isinstance(target, int) or not 3 <= target <= 7:
            raise ValueError("target must be between 3 and 7")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not target <= maximum <= 7:
            raise ValueError("maximum must be between target and 7")
        values = tuple(entries)
        if len(values) > maximum:
            raise ValueError("portfolio cannot contain more than maximum entries")
        if any(not isinstance(entry, PortfolioEntry) for entry in values):
            raise TypeError("entries must contain PortfolioEntry values")
        if any(entry.portfolio_id != identity for entry in values):
            raise ValueError("portfolio_id on every entry must match the portfolio")
        now = created_at or datetime.now(UTC)
        now_text = _utc_text(now)
        snapshot = _canonical_mapping_json(
            {
                "portfolio_id": identity,
                "run_id": run_id,
                "status": state,
                "target": target,
                "maximum": maximum,
                "state_version": 0,
                "entry_ids": [entry.entry_id for entry in values],
                "entry_snapshots": [
                    json.loads(self._portfolio_entry_snapshot(entry)) for entry in values
                ],
            }
        )
        with _immediate_transaction(self.connection):
            existing = self.connection.execute(
                "SELECT snapshot_json FROM portfolios WHERE portfolio_id = ?", (identity,)
            ).fetchone()
            if existing is not None:
                if str(existing["snapshot_json"]) != snapshot:
                    raise RepositoryConflict(
                        "portfolio identity already exists with different content"
                    )
                return False
            self.connection.execute(
                """
                INSERT INTO portfolios (
                    portfolio_id, run_id, status, target, maximum, state_version,
                    created_at, updated_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (identity, run_id, state, target, maximum, now_text, now_text, snapshot),
            )
            for entry in values:
                self.connection.execute(
                    """
                    INSERT INTO portfolio_entries (
                        entry_id, portfolio_id, candidate_id, rank, state,
                        state_version, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.entry_id,
                        identity,
                        entry.candidate_id,
                        entry.rank,
                        entry.state.value,
                        entry.state_version,
                        self._portfolio_entry_snapshot(entry),
                    ),
                )
        return True

    def get_portfolio_entries(self, portfolio_id: str) -> tuple[PortfolioEntry, ...]:
        identity = _require_nonblank(portfolio_id, "portfolio_id")
        rows = self.connection.execute(
            "SELECT snapshot_json FROM portfolio_entries WHERE portfolio_id = ? ORDER BY rank, entry_id",
            (identity,),
        ).fetchall()
        return tuple(PortfolioEntry.model_validate_json(str(row["snapshot_json"])) for row in rows)

    def get_portfolio_entry(self, entry_id: str) -> PortfolioEntry | None:
        identity = _require_nonblank(entry_id, "entry_id")
        row = self.connection.execute(
            "SELECT snapshot_json FROM portfolio_entries WHERE entry_id = ?", (identity,)
        ).fetchone()
        return (
            None if row is None else PortfolioEntry.model_validate_json(str(row["snapshot_json"]))
        )

    def get_portfolio(self, portfolio_id: str) -> dict[str, Any] | None:
        identity = _require_nonblank(portfolio_id, "portfolio_id")
        row = self.connection.execute(
            "SELECT portfolio_id, run_id, status, target, maximum, state_version, "
            "created_at, updated_at, snapshot_json FROM portfolios WHERE portfolio_id = ?",
            (identity,),
        ).fetchone()
        if row is None:
            return None
        return {
            "portfolio_id": str(row["portfolio_id"]),
            "run_id": None if row["run_id"] is None else str(row["run_id"]),
            "status": str(row["status"]),
            "target": int(row["target"]),
            "maximum": int(row["maximum"]),
            "state_version": int(row["state_version"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "entries": self.get_portfolio_entries(identity),
        }

    def record_portfolio_delivery(
        self,
        *,
        portfolio_id: str,
        delivery_id: str,
        idempotency_key: str,
        status: str,
        snapshot: dict[str, Any] | None = None,
    ) -> bool:
        portfolio = _require_nonblank(portfolio_id, "portfolio_id")
        delivery = _require_nonblank(delivery_id, "delivery_id")
        key = _require_nonblank(idempotency_key, "idempotency_key")
        delivery_status = _require_nonblank(status, "status")
        if delivery_status not in {"pending", "sent", "failed"}:
            raise ValueError("portfolio delivery status must be pending, sent or failed")
        now_text = _utc_text(datetime.now(UTC))
        payload = dict(snapshot or {})
        payload.update(
            {
                "portfolio_id": portfolio,
                "delivery_id": delivery,
                "idempotency_key": key,
                "status": delivery_status,
            }
        )
        snapshot_json = _canonical_mapping_json(payload)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT snapshot_json FROM portfolio_deliveries WHERE portfolio_id = ?",
                (portfolio,),
            ).fetchone()
            if row is not None:
                try:
                    existing_payload = json.loads(str(row["snapshot_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise RepositoryError("portfolio delivery snapshot is invalid") from error
                if not isinstance(existing_payload, dict):
                    raise RepositoryError("portfolio delivery snapshot must be an object")
                immutable_existing = {
                    key_item: value
                    for key_item, value in existing_payload.items()
                    if key_item not in {"status", "created_at", "updated_at"}
                }
                immutable_incoming = {
                    key_item: value
                    for key_item, value in payload.items()
                    if key_item not in {"status", "created_at", "updated_at"}
                }
                if immutable_existing != immutable_incoming:
                    raise RepositoryConflict(
                        "portfolio delivery already exists with different content"
                    )
                return False
            self.connection.execute(
                """
                INSERT INTO portfolio_deliveries (
                    portfolio_id, delivery_id, idempotency_key, status,
                    created_at, updated_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (portfolio, delivery, key, delivery_status, now_text, now_text, snapshot_json),
            )
        return True

    def get_portfolio_delivery(self, portfolio_id: str) -> dict[str, Any] | None:
        """Return the durable delivery envelope for one portfolio."""

        portfolio = _require_nonblank(portfolio_id, "portfolio_id")
        row = self.connection.execute(
            """
            SELECT portfolio_id, delivery_id, idempotency_key, status,
                   created_at, updated_at, snapshot_json
            FROM portfolio_deliveries WHERE portfolio_id = ?
            """,
            (portfolio,),
        ).fetchone()
        if row is None:
            return None
        try:
            snapshot = json.loads(str(row["snapshot_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RepositoryError("portfolio delivery snapshot is invalid") from error
        if not isinstance(snapshot, dict):
            raise RepositoryError("portfolio delivery snapshot must be an object")
        return {
            "portfolio_id": str(row["portfolio_id"]),
            "delivery_id": str(row["delivery_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "snapshot": snapshot,
        }

    def transition_portfolio_delivery(
        self,
        portfolio_id: str,
        *,
        expected_status: str,
        status: str,
    ) -> bool:
        """Compare-and-swap a delivery status without changing its message identity."""

        portfolio = _require_nonblank(portfolio_id, "portfolio_id")
        expected = _require_nonblank(expected_status, "expected_status")
        replacement = _require_nonblank(status, "status")
        if expected not in {"pending", "sent", "failed"} or replacement not in {
            "pending",
            "sent",
            "failed",
        }:
            raise ValueError("portfolio delivery status must be pending, sent or failed")
        now_text = _utc_text(datetime.now(UTC))
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT status, snapshot_json FROM portfolio_deliveries WHERE portfolio_id = ?",
                (portfolio,),
            ).fetchone()
            if row is None:
                raise KeyError(portfolio)
            current = str(row["status"])
            if current == replacement:
                return True
            if current != expected:
                raise RepositoryConflict("portfolio delivery state changed before transition")
            try:
                payload = json.loads(str(row["snapshot_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise RepositoryError("portfolio delivery snapshot is invalid") from error
            if not isinstance(payload, dict):
                raise RepositoryError("portfolio delivery snapshot must be an object")
            payload["status"] = replacement
            payload["updated_at"] = now_text
            cursor = self.connection.execute(
                """
                UPDATE portfolio_deliveries
                SET status = ?, updated_at = ?, snapshot_json = ?
                WHERE portfolio_id = ? AND status = ? AND snapshot_json = ?
                """,
                (
                    replacement,
                    now_text,
                    _canonical_mapping_json(payload),
                    portfolio,
                    expected,
                    str(row["snapshot_json"]),
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("portfolio delivery state changed before transition")
        return True

    @staticmethod
    def _dashboard_receipt(row: sqlite3.Row) -> DashboardActionReceipt:
        response: dict[str, Any] | None = None
        if row["response_json"] is not None:
            value = json.loads(str(row["response_json"]))
            if not isinstance(value, dict):
                raise RepositoryError("dashboard action response must be an object")
            response = value
        return DashboardActionReceipt(
            idempotency_key=str(row["idempotency_key"]),
            action=str(row["action"]),
            target_id=str(row["target_id"]),
            actor_id=str(row["actor_id"]),
            expected_state_version=int(row["expected_state_version"]),
            status=str(row["status"]),
            event_id=str(row["event_id"] or ""),
            response=response,
            error_code=None if row["error_code"] is None else str(row["error_code"]),
        )

    def claim_dashboard_action(
        self,
        *,
        idempotency_key: str,
        action: str,
        target_id: str,
        actor_id: str,
        expected_state_version: int,
    ) -> DashboardActionReceipt:
        key = _require_nonblank(idempotency_key, "idempotency_key")
        action_name = _require_nonblank(action, "action")
        target = _require_nonblank(target_id, "target_id")
        actor = _require_nonblank(actor_id, "actor_id")
        if (
            isinstance(expected_state_version, bool)
            or not isinstance(expected_state_version, int)
            or expected_state_version < 0
        ):
            raise ValueError("expected_state_version must be a non-negative integer")
        now_text = _utc_text(datetime.now(UTC))
        event_id = f"dashboard-event-{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}"
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is not None:
                same_identity = (
                    str(row["action"]) == action_name
                    and str(row["target_id"]) == target
                    and str(row["actor_id"]) == actor
                    and int(row["expected_state_version"]) == expected_state_version
                )
                if not same_identity:
                    raise RepositoryConflict(
                        "dashboard idempotency key has a different action identity"
                    )
                return self._dashboard_receipt(row)
            self.connection.execute(
                """
                INSERT INTO dashboard_action_receipts (
                    idempotency_key, action, target_id, actor_id,
                    expected_state_version, status, event_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    key,
                    action_name,
                    target,
                    actor,
                    expected_state_version,
                    event_id,
                    now_text,
                    now_text,
                ),
            )
            inserted = self.connection.execute(
                "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if inserted is None:
                raise RepositoryError("dashboard action receipt disappeared after insert")
            return self._dashboard_receipt(inserted)

    def get_dashboard_action_receipt(self, idempotency_key: str) -> DashboardActionReceipt | None:
        key = _require_nonblank(idempotency_key, "idempotency_key")
        row = self.connection.execute(
            "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return None if row is None else self._dashboard_receipt(row)

    def complete_dashboard_action(
        self,
        idempotency_key: str,
        *,
        status: str,
        response: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> DashboardActionReceipt:
        key = _require_nonblank(idempotency_key, "idempotency_key")
        if status not in {"pending", "succeeded", "failed"}:
            raise ValueError("dashboard action status must be pending, succeeded or failed")
        if response is not None and not isinstance(response, dict):
            raise TypeError("dashboard action response must be an object")
        response_json = None if response is None else _canonical_mapping_json(response)
        now_text = _utc_text(datetime.now(UTC))
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is None:
                raise KeyError(key)
            if str(row["status"]) != "pending":
                if str(row["status"]) != status or str(row["response_json"] or "") != str(
                    response_json or ""
                ):
                    raise RepositoryConflict("dashboard action receipt is already completed")
                return self._dashboard_receipt(row)
            self.connection.execute(
                """
                UPDATE dashboard_action_receipts
                SET status = ?, response_json = ?, error_code = ?, updated_at = ?
                WHERE idempotency_key = ? AND status = 'pending'
                """,
                (status, response_json, error_code, now_text, key),
            )
            updated = self.connection.execute(
                "SELECT * FROM dashboard_action_receipts WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if updated is None:
                raise RepositoryError("dashboard action receipt disappeared after update")
            return self._dashboard_receipt(updated)
