"""Safe, runtime-neutral Discord Components v2 project actions."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn, Protocol, cast

from openclaw_web.models import ComponentSet

MINH_ID = "620891893659598850"
WIEN_ID = "859783610625556480"
REVIEW_CARD_VERSION = "v1"
DEFAULT_REVIEW_REJECTION_REASON = "Không phù hợp với tiêu chí review hiện tại."


@dataclass(frozen=True, slots=True)
class Button:
    label: str
    action: str
    allowed_users: tuple[str, ...]

    @property
    def style(self) -> str:
        return {
            "approve": "success",
            "page-approve": "success",
            "final-confirm": "success",
            "reject": "danger",
            "block": "danger",
        }.get(self.action, "secondary")


def _component_value(project_id: str, action: str, page_slug: str | None) -> str:
    if page_slug is not None:
        return f"project:{project_id}:page:{page_slug}:{action}"
    return f"project:{project_id}:{action}"


@dataclass(frozen=True, slots=True)
class Card:
    project_id: str
    card_type: str
    buttons: tuple[Button, ...]
    page_slug: str | None = None

    @property
    def payload(self) -> dict[str, object]:
        return {
            "reusable": True,
            "blocks": [
                {
                    "type": "actions",
                    "buttons": [
                        {
                            "label": button.label,
                            "style": button.style,
                            "callbackData": (
                                "openclaw-web:"
                                + _component_value(
                                    self.project_id,
                                    button.action,
                                    self.page_slug,
                                )
                            ),
                            "callbackDataKind": "callback",
                            "allowedUsers": list(button.allowed_users),
                        }
                        for button in self.buttons
                    ],
                }
            ],
        }


def build_review_card(project_id: str) -> Card:
    """Build the review card; authorization is duplicated server-side on callback."""
    return Card(project_id, "review", (
        Button("Approve", "approve", (MINH_ID, WIEN_ID)),
        Button("Reject", "reject", (MINH_ID,)),
        Button("View evidence", "view-evidence", (MINH_ID, WIEN_ID)),
        Button("Refresh", "refresh", (MINH_ID, WIEN_ID)),
    ))


def build_page_card(
    project_id: str,
    assigned_actor: str,
    reviewer: str,
    *,
    page_slug: str | None = None,
) -> Card:
    return Card(
        project_id,
        "page",
        (
            Button("Page status", "page-status", (MINH_ID, WIEN_ID)),
            Button("Mark done", "mark-done", (assigned_actor,)),
            Button("Approve page", "page-approve", (reviewer,)),
            Button("Refresh", "refresh", (MINH_ID, WIEN_ID)),
        ),
        page_slug,
    )


def build_final_card(project_id: str, actors: Sequence[str] = (MINH_ID, WIEN_ID)) -> Card:
    allowed = tuple(actors)
    return Card(project_id, "final", (Button("Final confirm", "final-confirm", allowed), Button("View unresolved", "view-unresolved", (MINH_ID, WIEN_ID)), Button("Refresh", "refresh", (MINH_ID, WIEN_ID))))


@dataclass(frozen=True, slots=True)
class ComponentSetRecord:
    component_set_id: str
    channel_id: str
    message_id: str
    project_id: str
    card_type: str
    allowed_actions: tuple[str, ...]
    expires_at: datetime
    state_version: int
    page_slug: str | None = None
    assigned_actor_id: str | None = None
    reviewer_id: str | None = None


def component_record(component: ComponentSet) -> ComponentSetRecord:
    payload = component.model_dump(mode="python")
    return ComponentSetRecord(
        component.component_set_id,
        component.channel_id,
        component.message_id,
        component.project_id,
        component.card_type,
        cast(tuple[str, ...], component.allowed_actions),
        component.expires_at,
        component.state_version,
        _optional_string(payload.get("page_slug")),
        _optional_string(payload.get("assigned_actor_id")),
        _optional_string(payload.get("reviewer_id")),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class ComponentActionEnvelope:
    actor_id: str
    channel_id: str
    component_set_id: str
    message_id: str
    project_id: str
    state_version: int
    action: str
    page_slug: str | None = None
    reason: str | None = None

    @property
    def fallback_command(self) -> str:
        return typed_fallback(
            self.project_id,
            self.action,
            page_slug=self.page_slug,
            reason=self.reason,
        )


_ENVELOPE_KEYS = frozenset(
    {
        "actor_id",
        "channel_id",
        "component_set_id",
        "message_id",
        "reason",
        "state_version",
        "value",
    }
)
_ENVELOPE_REQUIRED = _ENVELOPE_KEYS - {"reason"}
_PROJECT_ACTIONS = frozenset(
    {"approve", "reject", "request-changes", "final-confirm", "refresh", "view-evidence", "view-unresolved"}
)
_PAGE_ACTIONS = frozenset(
    {"page-status", "mark-done", "block", "page-approve", "refresh"}
)
_READ_ONLY_ACTIONS = frozenset({"refresh", "view-evidence", "view-unresolved"})


def _invalid_envelope() -> NoReturn:
    raise ValueError("invalid component action envelope")


def _strict_json_object(text: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _invalid_envelope()
            result[key] = value
        return result

    def reject_constant(_value: str) -> NoReturn:
        _invalid_envelope()

    try:
        decoded = json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        _invalid_envelope()
    if not isinstance(decoded, dict):
        _invalid_envelope()
    return decoded


def parse_component_action_json(text: str) -> ComponentActionEnvelope:
    """Parse one exact callback envelope from a file/stdin JSON document."""

    payload = _strict_json_object(text)
    if set(payload) - _ENVELOPE_KEYS or not _ENVELOPE_REQUIRED <= set(payload):
        _invalid_envelope()
    strings: dict[str, str] = {}
    for key in ("actor_id", "channel_id", "component_set_id", "message_id", "value"):
        value = payload[key]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            _invalid_envelope()
        strings[key] = value
    state_version = payload["state_version"]
    if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version < 0:
        _invalid_envelope()
    reason = payload.get("reason")
    if reason is not None and (
        not isinstance(reason, str) or not reason.strip() or reason != reason.strip()
    ):
        _invalid_envelope()

    parts = strings["value"].split(":")
    page_slug: str | None
    if len(parts) == 3 and parts[0] == "project" and parts[2] in _PROJECT_ACTIONS:
        _, project_id, action = parts
        page_slug = None
    elif (
        len(parts) == 5
        and parts[0] == "project"
        and parts[2] == "page"
        and parts[4] in _PAGE_ACTIONS
    ):
        _, project_id, _, page_slug, action = parts
    else:
        _invalid_envelope()
    if not project_id or page_slug == "":
        _invalid_envelope()
    return ComponentActionEnvelope(
        actor_id=strings["actor_id"],
        channel_id=strings["channel_id"],
        component_set_id=strings["component_set_id"],
        message_id=strings["message_id"],
        project_id=project_id,
        state_version=state_version,
        action=action,
        page_slug=page_slug,
        reason=reason,
    )


def typed_fallback(
    project_id: str,
    action: str,
    *,
    page_slug: str | None = None,
    reason: str | None = None,
) -> str:
    command = {
        "approve": "lead-approve",
        "reject": "lead-reject",
        "request-changes": "lead-request-change",
        "page-status": "page-status",
        "mark-done": "page-done",
        "page-approve": "page-approve",
        "block": "block",
        "final-confirm": "final-confirm",
    }.get(action, action)
    parts = [f"/{command}", project_id]
    if action in _PAGE_ACTIONS - {"refresh"}:
        parts.append(page_slug or "<page_slug>")
    if action in {"reject", "block"}:
        parts.append(reason or "<reason>")
    elif action == "request-changes":
        parts.append(reason or "<note>")
    return " ".join(parts)


class ComponentStore(Protocol):
    def get_component_set(self, channel_id: str, message_id: str) -> ComponentSetRecord | None: ...


class DurableComponentStore(ComponentStore, Protocol):
    def claim_component_action(
        self, component_set_id: str, actor_id: str, action: str
    ) -> bool: ...


class WorkflowCoordinator(Protocol):
    def execute(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class ActionResult:
    status: str
    message_vi: str
    fallback_command: str


def _allowed_users(component: ComponentSetRecord, action: str) -> tuple[str, ...]:
    cards = {
        "review": build_review_card(component.project_id),
        "page": build_page_card(
            component.project_id,
            component.assigned_actor_id or "",
            component.reviewer_id or "",
            page_slug=component.page_slug,
        ),
        "final": build_final_card(component.project_id),
    }
    for button in cards.get(component.card_type, Card("x", "unknown", ())).buttons:
        if button.action == action:
            return button.allowed_users
    return ()


class ComponentActionService:
    """Validate all callback fields before invoking the canonical coordinator."""
    def __init__(self, store: ComponentStore, coordinator: WorkflowCoordinator, *, state_version: Callable[[str], int] | None = None, gate_ready: Callable[[ComponentSetRecord], bool] | None = None, read_only_action: Callable[[ComponentSetRecord, str], str] | None = None, now: Callable[[], datetime] | None = None) -> None:
        self.store, self.coordinator = store, coordinator
        self.state_version, self.gate_ready = state_version, gate_ready
        self.read_only_action = read_only_action
        self.now = now or (lambda: datetime.now(UTC))
        self._processed: set[tuple[str, str, str]] = set()

    def execute(
        self,
        *,
        channel_id: str,
        message_id: str,
        actor_id: str,
        action: str,
        reason: str | None = None,
        page_slug: str | None = None,
    ) -> ActionResult:
        component = self.store.get_component_set(channel_id, message_id)
        fallback = typed_fallback(
            component.project_id if component else "<project_id>",
            action,
            page_slug=page_slug,
            reason=reason,
        )
        if component is None:
            return ActionResult(
                "unknown",
                "Thẻ này không còn hợp lệ; hãy dùng lệnh dự phòng.",
                fallback,
            )
        if action not in component.allowed_actions or actor_id not in _allowed_users(component, action):
            return ActionResult(
                "unauthorized",
                "Bạn không có quyền thực hiện thao tác này.",
                fallback,
            )
        if component.card_type == "page" and (
            not component.page_slug or page_slug != component.page_slug
        ):
            return ActionResult("stale", "Thẻ đã cũ; hãy làm mới trước khi thao tác.", fallback)
        if component.expires_at <= self.now():
            return ActionResult(
                "expired",
                "Thẻ đã hết hạn; hãy làm mới hoặc dùng lệnh dự phòng.",
                fallback,
            )
        if self.state_version is not None and self.state_version(component.project_id) != component.state_version:
            return ActionResult("stale", "Thẻ đã cũ; hãy làm mới trước khi thao tác.", fallback)
        if self.gate_ready is not None and not self.gate_ready(component):
            return ActionResult(
                "blocked",
                "Chưa đạt điều kiện checklist hoặc còn P0/P1; không thể thực hiện thao tác.",
                fallback,
            )
        if action in _READ_ONLY_ACTIONS:
            if self.read_only_action is None:
                return ActionResult(
                    "unavailable",
                    "Chưa cấu hình thao tác chỉ đọc; hãy dùng lệnh dự phòng hoặc yêu cầu tạo thẻ mới.",
                    fallback,
                )
            return ActionResult(
                "read-only",
                self.read_only_action(component, action),
                fallback,
            )
        idempotency_key = (component.component_set_id, actor_id, action)
        claim = getattr(self.store, "claim_component_action", None)
        if callable(claim):
            if not claim(component.component_set_id, actor_id, action):
                return ActionResult(
                    "already-processed", "Thao tác này đã được ghi nhận.", fallback
                )
        elif idempotency_key in self._processed:
            return ActionResult(
                "already-processed", "Thao tác này đã được ghi nhận.", fallback
            )
        try:
            self.coordinator.execute(
                project_id=component.project_id,
                action=action,
                actor_id=actor_id,
                reason=reason,
                page_slug=page_slug,
                component_set_id=component.component_set_id,
                state_version=component.state_version,
            )
        except Exception:
            release = getattr(self.store, "release_component_action", None)
            if callable(release):
                release(component.component_set_id, actor_id, action)
            raise
        self._processed.add(idempotency_key)
        return ActionResult("accepted", "Đã ghi nhận thao tác.", fallback)

    def execute_envelope(self, envelope: ComponentActionEnvelope) -> ActionResult:
        component = self.store.get_component_set(envelope.channel_id, envelope.message_id)
        fallback = envelope.fallback_command
        if component is None:
            return ActionResult(
                "unknown", "Thẻ này không còn hợp lệ; hãy dùng lệnh dự phòng.", fallback
            )
        if (
            component.component_set_id != envelope.component_set_id
            or component.project_id != envelope.project_id
            or component.state_version != envelope.state_version
        ):
            return ActionResult("stale", "Thẻ đã cũ; hãy làm mới trước khi thao tác.", fallback)
        return self.execute(
            channel_id=envelope.channel_id,
            message_id=envelope.message_id,
            actor_id=envelope.actor_id,
            action=envelope.action,
            reason=envelope.reason,
            page_slug=envelope.page_slug,
        )
