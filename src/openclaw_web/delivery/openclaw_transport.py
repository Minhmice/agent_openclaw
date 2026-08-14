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

    @staticmethod
    def _message_presentation(components: dict[str, object]) -> dict[str, object]:
        """Translate the persisted Discord v2 spec to OpenClaw's shared presentation shape."""

        blocks = components.get("blocks")
        if not isinstance(blocks, list):
            raise TypeError("delivery payload presentation blocks must be a list")
        presentation: dict[str, object] = {}
        title = components.get("title")
        if isinstance(title, str) and title.strip():
            presentation["title"] = title
        output_blocks: list[dict[str, object]] = []
        for raw_block in blocks:
            if not isinstance(raw_block, dict):
                continue
            block_type = raw_block.get("type")
            if block_type in {"text", "context", "divider", "buttons"}:
                output_blocks.append(dict(raw_block))
                continue
            if block_type != "actions":
                continue
            raw_buttons = raw_block.get("buttons")
            if not isinstance(raw_buttons, list):
                continue
            buttons: list[dict[str, object]] = []
            for raw_button in raw_buttons:
                if not isinstance(raw_button, dict):
                    continue
                label = raw_button.get("label")
                callback = raw_button.get("callbackData")
                if not isinstance(label, str) or not label.strip():
                    continue
                if not isinstance(callback, str) or not callback.strip():
                    continue
                callback_kind = raw_button.get("callbackDataKind")
                action_type = callback_kind if callback_kind in {"command", "callback"} else "callback"
                action_key = "command" if action_type == "command" else "value"
                button: dict[str, object] = {
                    "label": label,
                    "action": {"type": action_type, action_key: callback},
                    "reusable": True,
                }
                style = raw_button.get("style")
                if isinstance(style, str) and style:
                    button["style"] = style
                buttons.append(button)
            if buttons:
                output_blocks.append({"type": "buttons", "buttons": buttons})
        if output_blocks:
            presentation["blocks"] = output_blocks
        if not presentation:
            raise ValueError("delivery payload has no renderable presentation")
        return presentation

    @classmethod
    def _response_identity(
        cls, decoded: dict[str, object], expected_channel_id: str
    ) -> tuple[str, str] | None:
        """Find a valid Discord identity in the bounded OpenClaw response envelope."""

        pending: list[object] = [decoded]
        visited: set[int] = set()
        for _ in range(64):
            if not pending:
                break
            current = pending.pop(0)
            if not isinstance(current, (dict, list)):
                continue
            marker = id(current)
            if marker in visited:
                continue
            visited.add(marker)
            if isinstance(current, dict):
                message_id = next(
                    (
                        current.get(key)
                        for key in ("messageId", "message_id", "id")
                        if isinstance(current.get(key), str)
                    ),
                    None,
                )
                channel_id = next(
                    (
                        current.get(key)
                        for key in ("channelId", "channel_id", "chatId")
                        if isinstance(current.get(key), str)
                    ),
                    None,
                )
                if (
                    isinstance(message_id, str)
                    and cls._SNOWFLAKE.fullmatch(message_id) is not None
                    and channel_id == expected_channel_id
                ):
                    return message_id, channel_id
                for key in (
                    "payload",
                    "result",
                    "sendResult",
                    "send_result",
                    "data",
                    "receipt",
                    "results",
                    "items",
                    "parts",
                ):
                    child = current.get(key)
                    if isinstance(child, (dict, list)):
                        pending.append(child)
            else:
                pending.extend(current)
        return None

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
            self._message_presentation(envelope["components"]),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(presentation.encode("utf-8")) > 65_536:
            raise ValueError("delivery payload is too large for the OpenClaw CLI")
        message = envelope.get("message")
        if not isinstance(message, str) or not message.strip():
            message = f"Duyệt lead {delivery.project_id}"
        if len(message.encode("utf-8")) > 4_000:
            raise ValueError("delivery message is too large for the OpenClaw CLI")
        command = [
            *self.build_command(),
            "--target",
            f"channel:{delivery.channel_id}",
            "--message",
            message,
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
        identity = self._response_identity(decoded, delivery.channel_id)
        if identity is None:
            raise ValueError("OpenClaw response has an invalid identity")
        message_id, _channel_id = identity
        return SentMessage(
            message_id,
            f"https://discord.com/channels/{self.guild_id}/{delivery.channel_id}/{message_id}",
        )
