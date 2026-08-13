"""Safe, runtime-neutral Discord Components v2 project actions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

MINH_ID = "620891893659598850"
WIEN_ID = "859783610625556480"


@dataclass(frozen=True, slots=True)
class Button:
    label: str
    action: str
    allowed_users: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Card:
    project_id: str
    card_type: str
    buttons: tuple[Button, ...]

    @property
    def payload(self) -> dict[str, object]:
        return {"components": [{"type": "container", "components": [
            {"type": "button", "label": button.label, "custom_id": f"project:{self.project_id}:{button.action}", "allowed_users": list(button.allowed_users)}
            for button in self.buttons
        ]}]}


def build_review_card(project_id: str) -> Card:
    """Build the review card; authorization is duplicated server-side on callback."""
    return Card(project_id, "review", (
        Button("Approve", "approve", (MINH_ID, WIEN_ID)),
        Button("Request changes", "request-changes", (MINH_ID,)),
        Button("Reject", "reject", (MINH_ID,)),
        Button("View evidence", "view-evidence", (MINH_ID, WIEN_ID)),
        Button("Refresh", "refresh", (MINH_ID, WIEN_ID)),
    ))


def build_page_card(project_id: str, assigned_actor: str, reviewer: str) -> Card:
    return Card(project_id, "page", (
        Button("Page status", "page-status", (MINH_ID, WIEN_ID)), Button("Mark done", "mark-done", (assigned_actor,)),
        Button("Block", "block", (assigned_actor,)), Button("Approve page", "page-approve", (reviewer,)),
        Button("Refresh", "refresh", (MINH_ID, WIEN_ID)),
    ))


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


class ComponentStore(Protocol):
    def get_component_set(self, channel_id: str, message_id: str) -> ComponentSetRecord | None: ...


class WorkflowCoordinator(Protocol):
    def execute(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class ActionResult:
    status: str
    message_vi: str
    fallback_command: str


def _allowed_users(card_type: str, action: str) -> tuple[str, ...]:
    cards = {"review": build_review_card("x"), "page": build_page_card("x", MINH_ID, MINH_ID), "final": build_final_card("x")}
    for button in cards.get(card_type, Card("x", "unknown", ())).buttons:
        if button.action == action:
            return button.allowed_users
    return ()


class ComponentActionService:
    """Validate all callback fields before invoking the canonical coordinator."""
    def __init__(self, store: ComponentStore, coordinator: WorkflowCoordinator, *, state_version: Callable[[str], int] | None = None, gate_ready: Callable[[ComponentSetRecord], bool] | None = None, now: Callable[[], datetime] | None = None) -> None:
        self.store, self.coordinator = store, coordinator
        self.state_version, self.gate_ready = state_version, gate_ready
        self.now = now or (lambda: datetime.now(UTC))
        self._processed: set[tuple[str, str, str]] = set()

    def execute(self, *, channel_id: str, message_id: str, actor_id: str, action: str, reason: str | None = None) -> ActionResult:
        component = self.store.get_component_set(channel_id, message_id)
        fallback = f"/{action} " + (component.project_id if component else "<project-id>")
        if component is None:
            return ActionResult("unknown", "The nay khong con hop le; hay dung lenh typed.", fallback)
        if action not in component.allowed_actions or actor_id not in _allowed_users(component.card_type, action):
            return ActionResult("unauthorized", "Ban khong co quyen thuc hien thao tac nay.", fallback)
        if component.expires_at <= self.now():
            return ActionResult("expired", "The da het han; hay Refresh hoac dung lenh typed.", fallback)
        if self.state_version is not None and self.state_version(component.project_id) != component.state_version:
            return ActionResult("stale", "The da cu; hay Refresh truoc khi thao tac.", fallback)
        if self.gate_ready is not None and not self.gate_ready(component):
            return ActionResult("blocked", "Chua dat dieu kien checklist hoac P0/P1; khong the thuc hien thao tac.", fallback)
        idempotency_key = (component.component_set_id, actor_id, action)
        if idempotency_key in self._processed:
            return ActionResult("already-processed", "Thao tac nay da duoc ghi nhan.", fallback)
        self.coordinator.execute(project_id=component.project_id, action=action, actor_id=actor_id, reason=reason, component_set_id=component.component_set_id)
        self._processed.add(idempotency_key)
        return ActionResult("accepted", "Da ghi nhan thao tac.", fallback)
