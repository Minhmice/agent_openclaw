"""Safe OpenClaw transport through the fixed native-plugin CLI bridge."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from openclaw_web.models import DeliveryRecord


@dataclass(frozen=True, slots=True)
class SentMessage:
    message_id: str
    message_url: str


class OpenClawAgentTransport:
    _SNOWFLAKE = re.compile(r"^[0-9]{17,20}$")

    def __init__(self, *, guild_id: str, timeout_seconds: int = 60) -> None:
        if not isinstance(guild_id, str) or self._SNOWFLAKE.fullmatch(guild_id) is None:
            raise ValueError("guild_id must be a Discord snowflake")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 90
        ):
            raise ValueError("timeout_seconds must be between 1 and 90")
        self.guild_id = guild_id
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def build_command() -> list[str]:
        return [
            "openclaw",
            "message",
            "send",
            "--channel",
            "discord",
            "--json",
        ]

    def send(self, delivery: DeliveryRecord) -> SentMessage:
        payload_path = Path(delivery.payload_path)
        if not payload_path.is_file():
            raise FileNotFoundError("delivery payload path does not exist")
        if payload_path.stat().st_size > 131_072:
            raise ValueError("delivery payload is too large")
        try:
            envelope = json.loads(payload_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError("delivery payload is not valid UTF-8 JSON") from error
        if not isinstance(envelope, dict) or not isinstance(envelope.get("components"), dict):
            raise TypeError("delivery payload is missing presentation components")
        if self._SNOWFLAKE.fullmatch(delivery.channel_id) is None:
            raise ValueError("delivery channel_id must be a Discord snowflake")
        presentation = json.dumps(
            envelope["components"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(presentation.encode("utf-8")) > 65_536:
            raise ValueError("delivery payload is too large for the OpenClaw CLI")
        command = [
            *self.build_command(),
            "--target",
            f"channel:{delivery.channel_id}",
            "--message",
            f"Duyệt lead {delivery.project_id}",
            "--presentation",
            presentation,
        ]
        result = subprocess.run(
            command,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError("OpenClaw message send returned a non-zero exit status")
        try:
            decoded = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("openclaw response is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise TypeError("openclaw response must be a JSON object")
        message_id = decoded.get("messageId", decoded.get("message_id"))
        channel_id = decoded.get("channelId", decoded.get("channel_id"))
        if (
            not isinstance(message_id, str)
            or self._SNOWFLAKE.fullmatch(message_id) is None
            or channel_id != delivery.channel_id
        ):
            raise ValueError("OpenClaw response has an invalid identity")
        return SentMessage(
            message_id,
            f"https://discord.com/channels/{self.guild_id}/{delivery.channel_id}/{message_id}",
        )
