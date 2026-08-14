from __future__ import annotations

import json
from pathlib import Path

import pytest

from openclaw_web.delivery.openclaw_transport import OpenClawAgentTransport
from openclaw_web.models import DeliveryRecord, DeliveryState

GUILD_ID = "1446612692910739637"
CHANNEL_ID = "1536658476288450630"
MESSAGE_ID = "1537000000000000000"


def test_openclaw_transport_builds_core_message_command() -> None:
    command = OpenClawAgentTransport.build_command()

    assert command == [
        "openclaw",
        "message",
        "send",
        "--channel",
        "discord",
        "--json",
    ]


def test_openclaw_transport_sends_presentation_through_stdin_without_model(
    tmp_path: Path, monkeypatch
) -> None:
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps(
            {
                "component_set": {"project_id": "project-1"},
                "components": {
                    "title": "Duyệt lead",
                    "blocks": [
                        {
                            "type": "buttons",
                            "buttons": [
                                {
                                    "label": "Approve",
                                    "action": {
                                        "type": "command",
                                        "command": "/approve project-1",
                                    },
                                }
                            ],
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object):
        seen["argv"] = argv
        seen["input"] = kwargs.get("input")
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "messageId": MESSAGE_ID,
                        "channelId": CHANNEL_ID,
                        "receipt": {"kind": "card"},
                    }
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("openclaw_web.delivery.openclaw_transport.subprocess.run", fake_run)
    record = DeliveryRecord(
        delivery_id="delivery-1",
        event_type="review-card",
        project_id="project-1",
        channel_id=CHANNEL_ID,
        payload_path=str(payload),
        idempotency_key="review:project-1",
        status=DeliveryState.PENDING,
    )

    sent = OpenClawAgentTransport(guild_id=GUILD_ID).send(record)

    argv = seen["argv"]
    assert argv[:7] == [
        "openclaw",
        "message",
        "send",
        "--channel",
        "discord",
        "--json",
        "--target",
    ]
    assert argv[7:9] == [f"channel:{CHANNEL_ID}", "--message"]
    assert argv[9:11] == ["Duyệt lead project-1", "--presentation"]
    assert json.loads(str(argv[11])) == json.loads(payload.read_text(encoding="utf-8"))["components"]
    assert seen["input"] is None
    assert sent.message_id == MESSAGE_ID
    assert sent.message_url == (
        f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{MESSAGE_ID}"
    )


def test_openclaw_transport_rejects_mismatched_response_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = tmp_path / "delivery.json"
    payload.write_text(
        json.dumps({"components": {"reusable": True, "blocks": []}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openclaw_web.delivery.openclaw_transport.subprocess.run",
        lambda *_args, **_kwargs: type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {"messageId": MESSAGE_ID, "channelId": "1536658476288450631"}
                ),
                "stderr": "",
            },
        )(),
    )
    record = DeliveryRecord(
        delivery_id="delivery-1",
        event_type="review-card",
        project_id="project-1",
        channel_id=CHANNEL_ID,
        payload_path=str(payload),
        idempotency_key="review:project-1",
        status=DeliveryState.PENDING,
    )

    with pytest.raises(ValueError, match="identity"):
        OpenClawAgentTransport(guild_id=GUILD_ID).send(record)
