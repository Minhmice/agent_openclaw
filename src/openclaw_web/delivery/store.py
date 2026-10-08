"""Transactional delivery store for deliveries, component sets, and project reviews."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from openclaw_web.platform.base import (
    BaseStore,
    _canonical_json,
    _canonical_mapping_json,
    _deserialize,
    _immediate_transaction,
    _require_nonblank,
    _utc_text,
)
from openclaw_web.models import (
    ComponentSet,
    DeliveryRecord,
    DeliveryState,
    FeedbackEvent,
    ProjectState,
)
from openclaw_web.platform.errors import RepositoryConflict, RepositoryError

_PROJECT_SNAPSHOT_SYNC_KEYS = frozenset({"lead_state", "pages"})
_PAGE_SNAPSHOT_SYNC_KEYS = frozenset(
    {
        "slug",
        "status",
        "owner_id",
        "assignee_id",
        "owner",
        "assignee",
        "checklist_complete",
        "next_action",
    }
)
_SAFE_SNAPSHOT_CODE = re.compile(r"^[A-Za-z0-9_.:@/ +()\-]{1,200}$")


def _validated_project_snapshot_updates(
    updates: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate the tiny snapshot subset the dashboard read model may receive."""

    if updates is None:
        return {}
    if not isinstance(updates, Mapping):
        raise TypeError("snapshot_updates must be a mapping")

    unknown_keys = set(updates) - _PROJECT_SNAPSHOT_SYNC_KEYS
    if unknown_keys:
        raise ValueError(f"snapshot_updates contains non-allowlisted field: {min(unknown_keys)}")

    normalized: dict[str, Any] = {}
    if "lead_state" in updates:
        lead_state = updates["lead_state"]
        if (
            not isinstance(lead_state, str)
            or not lead_state.strip()
            or _SAFE_SNAPSHOT_CODE.fullmatch(lead_state) is None
        ):
            raise ValueError("snapshot_updates.lead_state must be a safe non-empty string")
        normalized["lead_state"] = lead_state

    if "pages" in updates:
        pages = updates["pages"]
        if not isinstance(pages, (list, tuple)) or len(pages) > 50:
            raise ValueError("snapshot_updates.pages must contain at most 50 pages")
        normalized_pages: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, Mapping):
                raise TypeError("snapshot_updates.pages must contain objects")
            unknown_page = set(page) - _PAGE_SNAPSHOT_SYNC_KEYS
            if unknown_page:
                raise ValueError(
                    f"snapshot_updates.pages contains non-allowlisted field: {min(unknown_page)}"
                )
            slug = page.get("slug")
            status = page.get("status")
            if (
                not isinstance(slug, str)
                or not slug.strip()
                or not isinstance(status, str)
                or _SAFE_SNAPSHOT_CODE.fullmatch(slug) is None
                or _SAFE_SNAPSHOT_CODE.fullmatch(status) is None
            ):
                raise ValueError("snapshot_updates.pages requires safe slug and status")
            normalized_page: dict[str, Any] = {"slug": slug, "status": status}
            for key in _PAGE_SNAPSHOT_SYNC_KEYS - {"slug", "status"}:
                if key not in page or page[key] is None:
                    continue
                value = page[key]
                if key == "checklist_complete":
                    if not isinstance(value, bool):
                        raise TypeError("snapshot_updates.checklist_complete must be boolean")
                elif not isinstance(value, str) or _SAFE_SNAPSHOT_CODE.fullmatch(value) is None:
                    raise ValueError(f"snapshot_updates.pages.{key} is invalid")
                normalized_page[key] = value
            normalized_pages.append(normalized_page)
        normalized["pages"] = normalized_pages
    return normalized


def _delivery_immutable_identity(delivery: DeliveryRecord) -> tuple[str, ...]:
    return (
        delivery.delivery_id,
        delivery.event_type,
        delivery.project_id,
        delivery.channel_id,
        delivery.payload_path,
        delivery.idempotency_key,
    )


class ReviewProjectRecord(Protocol):
    """Structural input accepted from the production pipeline without an import cycle."""

    project_id: str
    candidate_id: str
    artifact_dir: str
    market_id: str
    created_at: datetime


class DeliveryStore(BaseStore):
    """Delivery persistence for outbox messages, component sets, and projects."""

    def enqueue_delivery(self, delivery: DeliveryRecord) -> DeliveryRecord:
        snapshot = _canonical_json(delivery)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT snapshot_json FROM deliveries
                WHERE delivery_id = ? OR idempotency_key = ?
                LIMIT 1
                """,
                (delivery.delivery_id, delivery.idempotency_key),
            ).fetchone()
            if row is not None:
                persisted = _deserialize(DeliveryRecord, str(row["snapshot_json"]))
                if _delivery_immutable_identity(persisted) != _delivery_immutable_identity(
                    delivery
                ):
                    raise RepositoryConflict(
                        "delivery identity already exists with different immutable content"
                    )
                return persisted
            self.connection.execute(
                """
                INSERT INTO deliveries (
                    delivery_id, idempotency_key, project_id, channel_id, status, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery.delivery_id,
                    delivery.idempotency_key,
                    delivery.project_id,
                    delivery.channel_id,
                    delivery.status.value,
                    snapshot,
                ),
            )
        return delivery

    def enqueue_delivery_once(self, delivery: DeliveryRecord) -> bool:
        """Idempotently enqueue a delivery and report whether it was newly inserted."""

        snapshot = _canonical_json(delivery)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT snapshot_json FROM deliveries
                WHERE delivery_id = ? OR idempotency_key = ?
                LIMIT 1
                """,
                (delivery.delivery_id, delivery.idempotency_key),
            ).fetchone()
            if row is not None:
                persisted = _deserialize(DeliveryRecord, str(row["snapshot_json"]))
                if _delivery_immutable_identity(persisted) != _delivery_immutable_identity(
                    delivery
                ):
                    raise RepositoryConflict(
                        "delivery identity already exists with different immutable content"
                    )
                return False
            self.connection.execute(
                """
                INSERT INTO deliveries (
                    delivery_id, idempotency_key, project_id, channel_id, status, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery.delivery_id,
                    delivery.idempotency_key,
                    delivery.project_id,
                    delivery.channel_id,
                    delivery.status.value,
                    snapshot,
                ),
            )
            return True

    def get_delivery(self, delivery_id: str) -> DeliveryRecord:
        row = self.connection.execute(
            "SELECT snapshot_json FROM deliveries WHERE delivery_id = ?",
            (_require_nonblank(delivery_id, "delivery_id"),),
        ).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        return _deserialize(DeliveryRecord, str(row["snapshot_json"]))

    def get_delivery_status(self, idempotency_key: str) -> DeliveryState | None:
        row = self.connection.execute(
            "SELECT status FROM deliveries WHERE idempotency_key = ?",
            (_require_nonblank(idempotency_key, "idempotency_key"),),
        ).fetchone()
        return None if row is None else DeliveryState(str(row["status"]))

    def transition_delivery(
        self,
        delivery_id: str,
        expected: DeliveryRecord,
        replacement: DeliveryRecord,
    ) -> DeliveryRecord:
        """Compare-and-swap a delivery using its expected persisted snapshot."""

        identity = _require_nonblank(delivery_id, "delivery_id")
        if not isinstance(expected, DeliveryRecord):
            raise TypeError("expected must be a DeliveryRecord")
        expected_state = expected.status
        if replacement.delivery_id != identity:
            raise ValueError("replacement delivery_id must match delivery_id")
        snapshot = _canonical_json(replacement)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT snapshot_json FROM deliveries WHERE delivery_id = ?",
                (identity,),
            ).fetchone()
            if row is None:
                raise KeyError(identity)
            persisted = _deserialize(DeliveryRecord, str(row["snapshot_json"]))
            if _delivery_immutable_identity(persisted) != _delivery_immutable_identity(replacement):
                raise RepositoryConflict("delivery immutable content changed")
            if persisted.status is not expected_state:
                raise RepositoryConflict("delivery state changed before transition")
            if persisted != expected:
                raise RepositoryConflict("delivery snapshot changed before transition")
            persisted_snapshot = str(row["snapshot_json"])
            cursor = self.connection.execute(
                """
                UPDATE deliveries
                SET status = ?, snapshot_json = ?
                WHERE delivery_id = ? AND status = ?
                  AND snapshot_json = ?
                """,
                (
                    replacement.status.value,
                    snapshot,
                    identity,
                    expected_state.value,
                    persisted_snapshot,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("delivery state changed before transition")
        return replacement

    def claim_next_delivery(self, *, max_attempts: int = 2) -> DeliveryRecord | None:
        """Atomically claim the oldest eligible pending or retryable failed delivery."""

        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT delivery_id, snapshot_json
                FROM deliveries
                WHERE status = 'pending'
                   OR (status = 'failed'
                       AND CAST(json_extract(snapshot_json, '$.attempt_count') AS INTEGER) < ?)
                ORDER BY rowid
                LIMIT 1
                """,
                (max_attempts,),
            ).fetchone()
            if row is None:
                return None
            persisted = _deserialize(DeliveryRecord, str(row["snapshot_json"]))
            claimed = persisted.validated_replace(status=DeliveryState.SENDING)
            cursor = self.connection.execute(
                """
                UPDATE deliveries
                SET status = 'sending', snapshot_json = ?
                WHERE delivery_id = ? AND status = ?
                """,
                (_canonical_json(claimed), persisted.delivery_id, persisted.status.value),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("delivery state changed before claim")
            return claimed

    def ensure_review_project(self, project: ReviewProjectRecord) -> bool:
        """Persist one production review project with immutable, restart-safe identity."""

        project_id = _require_nonblank(project.project_id, "project_id")
        candidate_id = _require_nonblank(project.candidate_id, "candidate_id")
        snapshot = _canonical_mapping_json(
            {
                "artifact_dir": _require_nonblank(project.artifact_dir, "artifact_dir"),
                "candidate_id": candidate_id,
                "created_at": _utc_text(project.created_at),
                "market_id": _require_nonblank(project.market_id, "market_id"),
                "project_id": project_id,
                "state": ProjectState.REVIEW.value,
                "state_version": 0,
            }
        )
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT candidate_id, snapshot_json FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if row is not None:
                persisted_snapshot = json.loads(str(row["snapshot_json"]))
                if not isinstance(persisted_snapshot, dict):
                    raise RepositoryError("project snapshot must be a JSON object")
                immutable_snapshot = {
                    key: value
                    for key, value in persisted_snapshot.items()
                    if key not in {"state", "state_version"}
                }
                expected_snapshot = json.loads(snapshot)
                expected_immutable = {
                    key: value
                    for key, value in expected_snapshot.items()
                    if key not in {"state", "state_version"}
                }
                if (
                    str(row["candidate_id"]) != candidate_id
                    or immutable_snapshot != expected_immutable
                ):
                    raise RepositoryConflict(
                        "project identity already exists with different immutable content"
                    )
                return False
            self.connection.execute(
                """
                INSERT INTO projects (
                    project_id, candidate_id, state, state_version, snapshot_json
                ) VALUES (?, ?, ?, 0, ?)
                """,
                (project_id, candidate_id, ProjectState.REVIEW.value, snapshot),
            )
        return True

    def synchronize_project_state(
        self,
        project_id: str,
        *,
        expected_version: int,
        state: ProjectState,
        state_version: int,
        snapshot_updates: Mapping[str, Any] | None = None,
    ) -> bool:
        """Synchronize coordinator state and an allowlisted read-model snapshot subset."""

        identity = _require_nonblank(project_id, "project_id")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise ValueError("expected_version must be a non-negative integer")
        if (
            isinstance(state_version, bool)
            or not isinstance(state_version, int)
            or state_version <= expected_version
        ):
            raise ValueError("state_version must be greater than expected_version")
        if not isinstance(state, ProjectState):
            raise TypeError("state must be a ProjectState")
        safe_updates = _validated_project_snapshot_updates(snapshot_updates)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                "SELECT snapshot_json FROM projects WHERE project_id = ? AND state_version = ?",
                (identity, expected_version),
            ).fetchone()
            if row is None:
                return False
            snapshot = json.loads(str(row["snapshot_json"]))
            if not isinstance(snapshot, dict):
                raise RepositoryError("project snapshot must be a JSON object")
            snapshot.update({"state": state.value, "state_version": state_version})
            snapshot.update(safe_updates)
            cursor = self.connection.execute(
                """
                UPDATE projects
                SET state = ?, state_version = ?, snapshot_json = ?
                WHERE project_id = ? AND state_version = ?
                """,
                (
                    state.value,
                    state_version,
                    _canonical_mapping_json(snapshot),
                    identity,
                    expected_version,
                ),
            )
            return cursor.rowcount == 1

    def insert_component_set(self, component: ComponentSet) -> ComponentSet:
        """Persist one bot-owned component set without changing its identity."""

        snapshot = _canonical_json(component)
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT snapshot_json FROM component_sets
                WHERE component_set_id = ? OR (channel_id = ? AND message_id = ?)
                LIMIT 1
                """,
                (component.component_set_id, component.channel_id, component.message_id),
            ).fetchone()
            if row is not None:
                persisted = _deserialize(ComponentSet, str(row["snapshot_json"]))
                if persisted != component:
                    raise RepositoryConflict(
                        "component identity already exists with different immutable content"
                    )
                return persisted
            self.connection.execute(
                """
                INSERT INTO component_sets (
                    component_set_id, channel_id, message_id, project_id,
                    expires_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    component.component_set_id,
                    component.channel_id,
                    component.message_id,
                    component.project_id,
                    _utc_text(component.expires_at),
                    snapshot,
                ),
            )
        return component

    def get_component_set(self, channel_id: str, message_id: str) -> ComponentSet | None:
        row = self.connection.execute(
            """
            SELECT snapshot_json FROM component_sets
            WHERE channel_id = ? AND message_id = ?
            """,
            (
                _require_nonblank(channel_id, "channel_id"),
                _require_nonblank(message_id, "message_id"),
            ),
        ).fetchone()
        return None if row is None else _deserialize(ComponentSet, str(row["snapshot_json"]))

    def get_component_set_by_message_id(self, message_id: str) -> ComponentSet | None:
        """Resolve one bot message globally and fail closed on legacy ambiguity."""

        rows = self.connection.execute(
            """
            SELECT snapshot_json FROM component_sets
            WHERE message_id = ?
            LIMIT 2
            """,
            (_require_nonblank(message_id, "message_id"),),
        ).fetchall()
        if len(rows) > 1:
            raise RepositoryConflict("component message identity is ambiguous")
        return None if not rows else _deserialize(ComponentSet, str(rows[0]["snapshot_json"]))

    def get_project_state_version(self, project_id: str) -> int:
        row = self.connection.execute(
            "SELECT state_version FROM projects WHERE project_id = ?",
            (_require_nonblank(project_id, "project_id"),),
        ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return int(row["state_version"])

    def claim_component_action(self, component_set_id: str, actor_id: str, action: str) -> bool:
        """Atomically claim a callback identity across process restarts."""

        with _immediate_transaction(self.connection):
            cursor = self.connection.execute(
                """
                INSERT INTO component_actions (
                    component_set_id, actor_id, action, claimed_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(component_set_id, actor_id, action) DO NOTHING
                """,
                (
                    _require_nonblank(component_set_id, "component_set_id"),
                    _require_nonblank(actor_id, "actor_id"),
                    _require_nonblank(action, "action"),
                    _utc_text(datetime.now(UTC)),
                ),
            )
            return cursor.rowcount == 1

    def release_component_action(self, component_set_id: str, actor_id: str, action: str) -> None:
        """Release a claim when coordinator execution failed before applying the action."""

        with _immediate_transaction(self.connection):
            self.connection.execute(
                """
                DELETE FROM component_actions
                WHERE component_set_id = ? AND actor_id = ? AND action = ?
                  AND confirmed_state IS NULL AND confirmed_state_version IS NULL
                """,
                (
                    _require_nonblank(component_set_id, "component_set_id"),
                    _require_nonblank(actor_id, "actor_id"),
                    _require_nonblank(action, "action"),
                ),
            )

    def confirm_component_action(
        self,
        component_set_id: str,
        actor_id: str,
        action: str,
        *,
        state: ProjectState,
        state_version: int,
    ) -> None:
        """Durably record coordinator authority before best-effort SQLite synchronization."""

        if not isinstance(state, ProjectState):
            raise TypeError("state must be a ProjectState")
        if (
            isinstance(state_version, bool)
            or not isinstance(state_version, int)
            or state_version < 0
        ):
            raise ValueError("state_version must be a non-negative integer")
        identity = (
            _require_nonblank(component_set_id, "component_set_id"),
            _require_nonblank(actor_id, "actor_id"),
            _require_nonblank(action, "action"),
        )
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT confirmed_state, confirmed_state_version
                FROM component_actions
                WHERE component_set_id = ? AND actor_id = ? AND action = ?
                """,
                identity,
            ).fetchone()
            if row is None:
                raise KeyError(identity)
            persisted = (row["confirmed_state"], row["confirmed_state_version"])
            confirmation = (state.value, state_version)
            if persisted[0] is not None or persisted[1] is not None:
                if persisted != confirmation:
                    raise RepositoryConflict(
                        "component action already has a different coordinator confirmation"
                    )
                return
            cursor = self.connection.execute(
                """
                UPDATE component_actions
                SET confirmed_state = ?, confirmed_state_version = ?
                WHERE component_set_id = ? AND actor_id = ? AND action = ?
                  AND confirmed_state IS NULL AND confirmed_state_version IS NULL
                """,
                (*confirmation, *identity),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("component action confirmation changed before update")

    def reconcile_component_action(self, component_set_id: str, actor_id: str, action: str) -> bool:
        """Apply a durable coordinator confirmation to lagging SQLite project state."""

        identity = (
            _require_nonblank(component_set_id, "component_set_id"),
            _require_nonblank(actor_id, "actor_id"),
            _require_nonblank(action, "action"),
        )
        with _immediate_transaction(self.connection):
            row = self.connection.execute(
                """
                SELECT cs.project_id, ca.confirmed_state, ca.confirmed_state_version
                FROM component_actions AS ca
                JOIN component_sets AS cs ON cs.component_set_id = ca.component_set_id
                WHERE ca.component_set_id = ? AND ca.actor_id = ? AND ca.action = ?
                """,
                identity,
            ).fetchone()
            if row is None or row["confirmed_state"] is None:
                return False
            state = ProjectState(str(row["confirmed_state"]))
            state_version = int(row["confirmed_state_version"])
            project_id = str(row["project_id"])
            project = self.connection.execute(
                "SELECT state, state_version, snapshot_json FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise KeyError(project_id)
            current_version = int(project["state_version"])
            if current_version > state_version:
                return True
            if current_version == state_version:
                if str(project["state"]) != state.value:
                    raise RepositoryConflict(
                        "coordinator confirmation conflicts with synchronized project state"
                    )
                return True
            snapshot = json.loads(str(project["snapshot_json"]))
            if not isinstance(snapshot, dict):
                raise RepositoryError("project snapshot must be a JSON object")
            snapshot.update({"state": state.value, "state_version": state_version})
            cursor = self.connection.execute(
                """
                UPDATE projects SET state = ?, state_version = ?, snapshot_json = ?
                WHERE project_id = ? AND state_version < ?
                """,
                (
                    state.value,
                    state_version,
                    _canonical_mapping_json(snapshot),
                    project_id,
                    state_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict("project state changed during reconciliation")
            return True

    def append_feedback(self, feedback: FeedbackEvent) -> None:
        snapshot = _canonical_json(feedback)
        self._append_snapshot(
            table="feedback",
            identity_column="event_id",
            record_id=feedback.event_id,
            model=feedback,
            model_type=FeedbackEvent,
            insert_sql="""
                INSERT INTO feedback (
                    event_id, project_id, actor_id, created_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?)
            """,
            values=(
                feedback.event_id,
                feedback.project_id,
                feedback.actor_id,
                _utc_text(feedback.created_at),
                snapshot,
            ),
        )
