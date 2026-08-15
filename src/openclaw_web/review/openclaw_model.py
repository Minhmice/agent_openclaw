"""OpenClaw CLI adapter for schema-constrained review prompts."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path


class OpenClawAgentModel:
    """Invoke an explicitly selected OpenClaw agent without shell interpolation."""

    ALLOWED_AGENTS = frozenset({"curie", "website-brief"})

    def __init__(self, agent_id: str = "curie", *, timeout_seconds: int = 300) -> None:
        if agent_id not in self.ALLOWED_AGENTS:
            raise ValueError("agent_id must be curie or website-brief")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise TypeError("timeout_seconds must be an integer")
        if timeout_seconds < 1 or timeout_seconds > 300:
            raise ValueError("timeout_seconds must be between 1 and 300")
        self.agent_id = agent_id
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def build_command(*, agent_id: str, prompt_path: Path, timeout_seconds: int) -> list[str]:
        if agent_id not in OpenClawAgentModel.ALLOWED_AGENTS:
            raise ValueError("agent_id must be curie or website-brief")
        return [
            "openclaw",
            "agent",
            "--agent",
            agent_id,
            "--message-file",
            str(prompt_path),
            "--timeout",
            str(timeout_seconds),
            "--json",
        ]

    def _invoke(self, prompt: str) -> str:
        descriptor, raw_path = tempfile.mkstemp(prefix="openclaw-review-", suffix=".txt")
        path = Path(raw_path)
        try:
            os.chmod(path, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                handle.write(prompt)
                handle.flush()
            completed = subprocess.run(
                self.build_command(
                    agent_id=self.agent_id,
                    prompt_path=path,
                    timeout_seconds=self.timeout_seconds,
                ),
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            if completed.returncode != 0:
                raise RuntimeError("openclaw agent returned a non-zero exit status")
            decoded: object = json.loads(completed.stdout)
            if isinstance(decoded, str):
                return decoded
            if isinstance(decoded, dict):
                for key in ("response", "content", "text", "message", "result"):
                    candidate = decoded.get(key)
                    if isinstance(candidate, str):
                        return candidate
            return str(json.dumps(decoded, ensure_ascii=False))
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            path.unlink(missing_ok=True)

    async def complete(self, prompt: str) -> str:
        return await asyncio.to_thread(self._invoke, prompt)
