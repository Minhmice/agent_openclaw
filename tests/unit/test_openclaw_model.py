from __future__ import annotations

from pathlib import Path

from openclaw_web.review.openclaw_model import OpenClawAgentModel


def test_openclaw_model_uses_message_file_and_argument_array(tmp_path: Path) -> None:
    command = OpenClawAgentModel.build_command(
        agent_id="curie", prompt_path=tmp_path / "prompt.txt", timeout_seconds=300
    )
    assert command == [
        "openclaw",
        "agent",
        "--agent",
        "curie",
        "--message-file",
        str(tmp_path / "prompt.txt"),
        "--timeout",
        "300",
        "--json",
    ]


def test_only_supported_agent_ids_are_allowed(tmp_path: Path) -> None:
    try:
        OpenClawAgentModel(agent_id="main")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported agent id must be rejected")

