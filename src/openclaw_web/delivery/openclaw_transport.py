"""Safe OpenClaw transport using a file-path-only handoff."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from openclaw_web.models import DeliveryRecord


@dataclass(frozen=True, slots=True)
class SentMessage:
    message_id: str
    message_url: str


class OpenClawAgentTransport:
    def __init__(self, *, timeout_seconds: int = 300) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 1 and 300")
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def build_command(payload_path: Path, *, timeout_seconds: int) -> list[str]:
        return ["openclaw", "agent", "--agent", "main", "--message-file", str(payload_path.with_suffix(".handoff.txt")), "--timeout", str(timeout_seconds), "--json"]

    def send(self, delivery: DeliveryRecord) -> SentMessage:
        payload_path = Path(delivery.payload_path)
        if not payload_path.is_file():
            raise FileNotFoundError("delivery payload path does not exist")
        handoff = payload_path.with_suffix(".handoff.txt")
        handoff.write_text(f"delivery_id={delivery.delivery_id}\npayload_path={payload_path}\n", encoding="utf-8")
        try:
            os.chmod(handoff, 0o600)
            result = subprocess.run(self.build_command(payload_path, timeout_seconds=self.timeout_seconds), shell=False, check=False, capture_output=True, text=True, timeout=self.timeout_seconds)
            if result.returncode != 0:
                raise RuntimeError("openclaw agent returned a non-zero exit status")
            decoded = json.loads(result.stdout)
            if not isinstance(decoded, dict):
                raise TypeError("openclaw response must be a JSON object")
            message_id = decoded.get("message_id") or decoded.get("id")
            message_url = decoded.get("message_url") or decoded.get("url")
            if not isinstance(message_id, str) or not message_id or not isinstance(message_url, str) or not message_url:
                raise ValueError("openclaw response is missing bot message identity")
            return SentMessage(message_id, message_url)
        finally:
            handoff.unlink(missing_ok=True)
