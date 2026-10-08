"""Resumable, content-addressed stage execution for audit workflows.

Defines ArtifactStageOutcome for filesystem checkpoints in audit pipelines.
This is distinct from openclaw_web.lead_intelligence.contracts.StageOutcome,
which is the canonical Pydantic model for multi-stage lead intelligence runs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENERATOR_VERSION = "pipeline-v1"

__all__ = [
    "GENERATOR_VERSION",
    "ArtifactStageOutcome",
    "StageRunner",
    "canonical_hash",
]


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactStageOutcome:
    """Filesystem checkpoint outcome for audit pipeline stages.

    Represents a disk-persisted artifact checkpoint for an audit pipeline stage.
    Distinct from openclaw_web.lead_intelligence.contracts.StageOutcome, which
    is the canonical database model for resumable lead runs in LeadStore.
    """

    stage_id: str
    stage_name: str
    status: str
    input_hash: str
    output_hash: str
    output_path: str
    reused: bool = False
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize artifact stage outcome to a dictionary."""
        return {
            "stage_id": self.stage_id,
            "stage_name": self.stage_name,
            "status": self.status,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "output_path": self.output_path,
            "reused": self.reused,
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArtifactStageOutcome:
        """Deserialize artifact stage outcome from a dictionary."""
        return cls(
            stage_id=str(data["stage_id"]),
            stage_name=str(data["stage_name"]),
            status=str(data["status"]),
            input_hash=str(data["input_hash"]),
            output_hash=str(data["output_hash"]),
            output_path=str(data["output_path"]),
            reused=bool(data.get("reused", False)),
            error_code=data.get("error_code") if data.get("error_code") is not None else None,
        )


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
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        temporary.replace(self.run_path)

    def run(
        self,
        stage_name: str,
        input_value: object,
        output_name: str,
        producer: Callable[[], object],
    ) -> ArtifactStageOutcome:
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
            return ArtifactStageOutcome(
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
                temporary.write_text(
                    json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2),
                    encoding="utf-8",
                )
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
            return ArtifactStageOutcome(
                stage_id, stage_name, "complete", input_hash, output_hash, output_name
            )
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
