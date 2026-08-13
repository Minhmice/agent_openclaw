from __future__ import annotations

from pathlib import Path

from openclaw_web.delivery.openclaw_transport import OpenClawAgentTransport


def test_openclaw_transport_hands_off_payload_by_path(tmp_path: Path) -> None:
    payload = tmp_path / "delivery.json"
    payload.write_text('{"delivery_id":"d1"}', encoding="utf-8")

    command = OpenClawAgentTransport.build_command(payload, timeout_seconds=300)

    assert command == [
        "openclaw", "agent", "--agent", "main", "--message-file",
        str(payload.with_suffix(".handoff.txt")), "--timeout", "300", "--json",
    ]

