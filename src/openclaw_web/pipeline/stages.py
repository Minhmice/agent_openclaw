"""Resumable, content-addressed stage execution for audit workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


GENERATOR_VERSION = "pipeline-v1"


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StageOutcome:
    stage_id: str
    stage_name: str
    status: str
    input_hash: str
    output_hash: str
    output_path: str
    reused: bool = False
    error_code: str | None = None


class StageRunner:
    """Run JSON-producing stages and reuse only matching successful checkpoints."""

    def __init__(self, project_root: Path, project_id: str) -> None:
        self.project_dir = Path(project_root) / project_id
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.run_path = self.project_dir / "run.json"
        self._stages: list[dict[str, Any]] = []
        if self.run_path.exists():
            try:
                document = json.loads(self.run_path.read_text(encoding="utf-8"))
                if isinstance(document, dict) and isinstance(document.get("stages"), list):
                    self._stages = [item for item in document["stages"] if isinstance(item, dict)]
            except (OSError, json.JSONDecodeError):
                self._stages = []

    def _persist(self) -> None:
        document = {
            "generator_version": GENERATOR_VERSION,
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "stages": self._stages,
        }
        temporary = self.run_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(self.run_path)

    def run(
        self,
        stage_name: str,
        input_value: object,
        output_name: str,
        producer: Callable[[], object],
    ) -> StageOutcome:
        input_hash = canonical_hash(input_value)
        stage_id = f"{stage_name}:{input_hash[:12]}"
        existing = next((item for item in self._stages if item.get("stage_id") == stage_id), None)
        output_path = self.project_dir / output_name
        if (
            existing
            and existing.get("status") == "complete"
            and existing.get("generator_version") == GENERATOR_VERSION
            and existing.get("checkpoint", {}).get("input_hash") == input_hash
            and output_path.exists()
        ):
            return StageOutcome(
                stage_id,
                stage_name,
                "complete",
                input_hash,
                str(existing.get("checkpoint", {}).get("output_hash", "")),
                output_name,
                True,
            )
        started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            output = producer()
            output_hash = canonical_hash(output)
            temporary = output_path.with_suffix(output_path.suffix + ".tmp")
            if output_name.endswith(".json"):
                temporary.write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            else:
                temporary.write_text(str(output), encoding="utf-8")
            temporary.replace(output_path)
            record = {
                "stage_id": stage_id,
                "stage_name": stage_name,
                "status": "complete",
                "generator_version": GENERATOR_VERSION,
                "started_at": started,
                "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "checkpoint": {"input_hash": input_hash, "output_hash": output_hash},
                "error_code": None,
            }
            self._stages = [item for item in self._stages if item.get("stage_name") != stage_name]
            self._stages.append(record)
            self._persist()
            return StageOutcome(stage_id, stage_name, "complete", input_hash, output_hash, output_name)
        except Exception:
            record = {
                "stage_id": stage_id,
                "stage_name": stage_name,
                "status": "failed-terminal",
                "generator_version": GENERATOR_VERSION,
                "started_at": started,
                "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "checkpoint": {"input_hash": input_hash},
                "error_code": "stage_failed",
            }
            self._stages = [item for item in self._stages if item.get("stage_name") != stage_name]
            self._stages.append(record)
            self._persist()
            raise

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._stages)
