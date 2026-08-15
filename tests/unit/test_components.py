import json
from datetime import UTC, datetime, timedelta

import pytest

from openclaw_web.delivery.components import (
    MINH_ID,
    WIEN_ID,
    ComponentActionService,
    ComponentSetRecord,
    build_final_card,
    build_page_card,
    build_review_card,
    parse_component_action_json,
)


class Components:
    def __init__(self, component: ComponentSetRecord) -> None:
        self.component = component

    def get_component_set(self, channel_id: str, message_id: str) -> ComponentSetRecord | None:
        return self.component if (channel_id, message_id) == (self.component.channel_id, self.component.message_id) else None


class DurableComponents(Components):
    def __init__(self, component: ComponentSetRecord) -> None:
        super().__init__(component)
        self.claims: set[tuple[str, str, str]] = set()

    def claim_component_action(
        self, component_set_id: str, actor_id: str, action: str
    ) -> bool:
        key = (component_set_id, actor_id, action)
        if key in self.claims:
            return False
        self.claims.add(key)
        return True

    def release_component_action(
        self, component_set_id: str, actor_id: str, action: str
    ) -> None:
        self.claims.discard((component_set_id, actor_id, action))


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
    assert "Request changes" not in buttons
    payload = card.payload
    assert set(payload) == {"reusable", "blocks"}
    assert payload["reusable"] is True
    assert payload["blocks"][0]["type"] == "actions"
    rendered = {button["label"]: button for button in payload["blocks"][0]["buttons"]}
    assert rendered["Approve"] == {
        "label": "Approve",
        "style": "success",
        "callbackData": "openclaw-web:project:project-1:approve",
        "callbackDataKind": "callback",
        "allowedUsers": [MINH_ID, WIEN_ID],
    }


def test_review_card_includes_min_only_reject_button() -> None:
    card = build_review_card("project-1")
    buttons = {button.label: button for button in card.buttons}

    reject = buttons["Reject"]
    assert reject.action == "reject"
    assert reject.allowed_users == (MINH_ID,)

    rendered = {button["label"]: button for button in card.payload["blocks"][0]["buttons"]}
    assert rendered["Reject"] == {
        "label": "Reject",
        "style": "danger",
        "callbackData": "openclaw-web:project:project-1:reject",
        "callbackDataKind": "callback",
        "allowedUsers": [MINH_ID],
    }


def test_page_card_carries_page_slug_in_each_page_action_value() -> None:
    card = build_page_card("project-1", MINH_ID, WIEN_ID, page_slug="homepage")
    rendered = {button["label"]: button for button in card.payload["blocks"][0]["buttons"]}

    assert rendered["Page status"]["callbackData"] == (
        "openclaw-web:project:project-1:page:homepage:page-status"
    )
    assert rendered["Mark done"]["callbackData"] == (
        "openclaw-web:project:project-1:page:homepage:mark-done"
    )
    assert rendered["Approve page"]["callbackData"] == (
        "openclaw-web:project:project-1:page:homepage:page-approve"
    )


def test_component_action_json_envelope_is_strict_and_extracts_button_context() -> None:
    envelope = parse_component_action_json(
        json.dumps(
            {
                "actor_id": WIEN_ID,
                "channel_id": "channel-1",
                "component_set_id": "set-1",
                "message_id": "message-1",
                "state_version": 2,
                "value": "project:project-1:page:homepage:page-approve",
            }
        )
    )

    assert envelope.project_id == "project-1"
    assert envelope.action == "page-approve"
    assert envelope.page_slug == "homepage"
    assert envelope.fallback_command == "/page-approve project-1 homepage"


def test_page_component_authorization_comes_from_persisted_assignees() -> None:
    coordinator = Coordinator()
    record = component(
        card_type="page",
        allowed_actions=("page-approve",),
        assigned_actor_id=WIEN_ID,
        reviewer_id=MINH_ID,
        page_slug="homepage",
    )
    service = ComponentActionService(Components(record), coordinator)

    rejected = service.execute(
        channel_id="channel-1",
        message_id="message-1",
        actor_id=WIEN_ID,
        action="page-approve",
        page_slug="homepage",
    )
    accepted = service.execute(
        channel_id="channel-1",
        message_id="message-1",
        actor_id=MINH_ID,
        action="page-approve",
        page_slug="homepage",
    )

    assert rejected.status == "unauthorized"
    assert accepted.status == "accepted"


def test_page_component_rejects_wrong_page_slug() -> None:
    coordinator = Coordinator()
    record = component(
        card_type="page",
        allowed_actions=("page-status",),
        assigned_actor_id=WIEN_ID,
        reviewer_id=MINH_ID,
        page_slug="homepage",
    )

    result = ComponentActionService(Components(record), coordinator).execute(
        channel_id="channel-1",
        message_id="message-1",
        actor_id=WIEN_ID,
        action="page-status",
        page_slug="pricing",
    )

    assert result.status == "stale"
    assert coordinator.calls == []


@pytest.mark.parametrize(
    ("action", "page_slug", "reason", "expected"),
    [
        ("approve", None, None, "/lead-approve project-1"),
        ("reject", None, None, "/lead-reject project-1 <reason>"),
        ("request-changes", None, None, "/lead-request-change project-1 <note>"),
        ("page-status", "home", None, "/page-status project-1 home"),
        ("mark-done", "home", None, "/page-done project-1 home"),
        ("page-approve", "home", None, "/page-approve project-1 home"),
        ("block", "home", None, "/block project-1 home <reason>"),
        ("final-confirm", None, None, "/final-confirm project-1"),
    ],
)
def test_component_typed_fallbacks_match_canonical_commands(
    action: str, page_slug: str | None, reason: str | None, expected: str
) -> None:
    value = (
        f"project:project-1:page:{page_slug}:{action}"
        if page_slug is not None
        else f"project:project-1:{action}"
    )
    envelope = parse_component_action_json(
        json.dumps(
            {
                "actor_id": MINH_ID,
                "channel_id": "channel-1",
                "component_set_id": "set-1",
                "message_id": "message-1",
                "reason": reason,
                "state_version": 2,
                "value": value,
            }
        )
    )

    assert envelope.fallback_command == expected


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"actor_id":"a","actor_id":"b"}',
        json.dumps(
            {
                "actor_id": MINH_ID,
                "channel_id": "channel-1",
                "component_set_id": "set-1",
                "message_id": "message-1",
                "state_version": 2,
                "unexpected": True,
                "value": "project:project-1:approve",
            }
        ),
    ],
)
def test_component_action_json_rejects_noncanonical_envelopes(payload: str) -> None:
    with pytest.raises(ValueError, match="component action envelope"):
        parse_component_action_json(payload)


def test_final_card_uses_project_level_final_confirm_callback() -> None:
    card = build_final_card("project-1")
    rendered = {button["label"]: button for button in card.payload["blocks"][0]["buttons"]}
    assert (
        rendered["Final confirm"]["callbackData"]
        == "openclaw-web:project:project-1:final-confirm"
    )
    assert rendered["Final confirm"]["callbackDataKind"] == "callback"
    assert rendered["Final confirm"]["allowedUsers"] == [MINH_ID, WIEN_ID]


def test_stale_component_action_never_reaches_coordinator() -> None:
    coordinator = Coordinator()
    result = ComponentActionService(Components(component()), coordinator, state_version=lambda _: 3).execute(
        channel_id="channel-1", message_id="message-1", actor_id=MINH_ID, action="approve"
    )
    assert result.status == "stale"
    assert coordinator.calls == []


def test_envelope_identity_mismatch_never_reaches_coordinator() -> None:
    coordinator = Coordinator()
    envelope = parse_component_action_json(
        json.dumps(
            {
                "actor_id": MINH_ID,
                "channel_id": "channel-1",
                "component_set_id": "different-set",
                "message_id": "message-1",
                "state_version": 2,
                "value": "project:project-1:approve",
            }
        )
    )

    result = ComponentActionService(Components(component()), coordinator).execute_envelope(envelope)

    assert result.status == "stale"
    assert coordinator.calls == []


def test_unauthorized_actor_never_reaches_coordinator() -> None:
    coordinator = Coordinator()
    result = ComponentActionService(Components(component()), coordinator).execute(
        channel_id="channel-1", message_id="message-1", actor_id="unauthorized", action="approve"
    )
    assert result.status == "unauthorized"
    assert result.fallback_command == "/lead-approve project-1"
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


def test_failed_coordinator_releases_durable_action_claim_for_retry() -> None:
    store = DurableComponents(component())

    class FailingCoordinator:
        def execute(self, **_kwargs: object) -> None:
            raise RuntimeError("coordinator failed")

    with pytest.raises(RuntimeError, match="coordinator failed"):
        ComponentActionService(store, FailingCoordinator()).execute(
            channel_id="channel-1",
            message_id="message-1",
            actor_id=MINH_ID,
            action="approve",
        )

    assert store.claims == set()


def test_duplicate_callback_remains_idempotent_across_service_instances() -> None:
    coordinator = Coordinator()
    store = DurableComponents(component())

    first = ComponentActionService(store, coordinator).execute(
        channel_id="channel-1", message_id="message-1", actor_id=MINH_ID, action="approve"
    )
    second = ComponentActionService(store, coordinator).execute(
        channel_id="channel-1", message_id="message-1", actor_id=MINH_ID, action="approve"
    )

    assert first.status == "accepted"
    assert second.status == "already-processed"
    assert len(coordinator.calls) == 1


@pytest.mark.parametrize("action", ["view-evidence", "view-unresolved", "refresh"])
def test_read_only_component_actions_use_separate_handler_without_coordinator(
    action: str,
) -> None:
    coordinator = Coordinator()
    store = DurableComponents(
        component(
            card_type="final" if action == "view-unresolved" else "review",
            allowed_actions=(action,),
        )
    )
    handled: list[tuple[str, str]] = []

    def read_only(record: ComponentSetRecord, requested_action: str) -> str:
        handled.append((record.project_id, requested_action))
        return f"Đã xử lý {requested_action}."

    result = ComponentActionService(
        store,
        coordinator,
        read_only_action=read_only,
    ).execute(
        channel_id="channel-1",
        message_id="message-1",
        actor_id=MINH_ID,
        action=action,
    )

    assert result.status == "read-only"
    assert result.message_vi == f"Đã xử lý {action}."
    assert coordinator.calls == []
    assert store.claims == set()
    assert handled == [("project-1", action)]
