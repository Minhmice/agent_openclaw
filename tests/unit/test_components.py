from datetime import UTC, datetime, timedelta

from openclaw_web.delivery.components import (
    MINH_ID,
    WIEN_ID,
    ComponentActionService,
    ComponentSetRecord,
    build_review_card,
)


class Components:
    def __init__(self, component: ComponentSetRecord) -> None:
        self.component = component

    def get_component_set(self, channel_id: str, message_id: str) -> ComponentSetRecord | None:
        return self.component if (channel_id, message_id) == (self.component.channel_id, self.component.message_id) else None


class Coordinator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def component(**overrides: object) -> ComponentSetRecord:
    payload: dict[str, object] = {
        "component_set_id": "set-1", "channel_id": "channel-1", "message_id": "message-1",
        "project_id": "project-1", "card_type": "review", "allowed_actions": ("approve", "reject", "request-changes"),
        "expires_at": datetime.now(UTC) + timedelta(hours=1), "state_version": 2,
    }
    payload.update(overrides)
    return ComponentSetRecord(**payload)


def test_review_card_applies_per_button_allowlists() -> None:
    card = build_review_card("project-1")
    buttons = {button.label: button for button in card.buttons}
    assert buttons["Approve"].allowed_users == (MINH_ID, WIEN_ID)
    assert buttons["Reject"].allowed_users == (MINH_ID,)
    assert buttons["Request changes"].allowed_users == (MINH_ID,)
    assert card.payload["components"][0]["type"] == "container"


def test_stale_component_action_never_reaches_coordinator() -> None:
    coordinator = Coordinator()
    result = ComponentActionService(Components(component()), coordinator, state_version=lambda _: 3).execute(
        channel_id="channel-1", message_id="message-1", actor_id=MINH_ID, action="approve"
    )
    assert result.status == "stale"
    assert coordinator.calls == []


def test_unauthorized_actor_never_reaches_coordinator() -> None:
    coordinator = Coordinator()
    result = ComponentActionService(Components(component()), coordinator).execute(
        channel_id="channel-1", message_id="message-1", actor_id="unauthorized", action="approve"
    )
    assert result.status == "unauthorized"
    assert result.fallback_command == "/approve project-1"
    assert coordinator.calls == []


def test_expired_component_action_never_reaches_coordinator() -> None:
    coordinator = Coordinator()
    result = ComponentActionService(Components(component(expires_at=datetime.now(UTC) - timedelta(seconds=1))), coordinator).execute(
        channel_id="channel-1", message_id="message-1", actor_id=MINH_ID, action="approve"
    )
    assert result.status == "expired"
    assert coordinator.calls == []


def test_failed_project_gate_never_reaches_coordinator() -> None:
    coordinator = Coordinator()
    result = ComponentActionService(Components(component()), coordinator, gate_ready=lambda _: False).execute(
        channel_id="channel-1", message_id="message-1", actor_id=MINH_ID, action="approve"
    )
    assert result.status == "blocked"
    assert coordinator.calls == []


def test_duplicate_callback_is_idempotent() -> None:
    coordinator = Coordinator()
    service = ComponentActionService(Components(component()), coordinator)
    first = service.execute(channel_id="channel-1", message_id="message-1", actor_id=MINH_ID, action="approve")
    second = service.execute(channel_id="channel-1", message_id="message-1", actor_id=MINH_ID, action="approve")
    assert first.status == "accepted"
    assert second.status == "already-processed"
    assert len(coordinator.calls) == 1
