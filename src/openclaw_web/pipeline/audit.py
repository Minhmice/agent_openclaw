"""Fixture-friendly audit orchestration across deterministic workflow stages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from openclaw_web.pipeline.stages import StageRunner


@dataclass(frozen=True, slots=True)
class AuditResult:
    project_id: str
    state: str
    artifacts: tuple[str, ...]
    completed_stage_ids: tuple[str, ...]
    reused_stage_ids: tuple[str, ...]


class AuditPipeline:
    """Run the local artifact spine and apply the first review-readiness gate."""

    def __init__(self, project_root: Path, *, generator_version: str = "pipeline-v1") -> None:
        self.project_root = Path(project_root)
        self.generator_version = generator_version

    async def audit(self, candidate: Mapping[str, object]) -> AuditResult:
        if not isinstance(candidate, Mapping):
            raise TypeError("candidate must be a mapping")
        project_id = candidate.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("candidate.project_id must be a non-empty string")
        runner = StageRunner(self.project_root, project_id)
        base = dict(candidate)
        stages = (
            ("candidate", "candidate.json", lambda: base),
            ("evidence", "evidence.json", lambda: {"project_id": project_id, "items": []}),
            ("pages", "pages.json", lambda: {"project_id": project_id, "pages": [{"url": base.get("url")}]}),
            ("scores", "scores.json", lambda: {"project_id": project_id, "lead_score": 0.0, "deterministic": True}),
            ("issues", "issues.json", lambda: {"project_id": project_id, "issues": []}),
            ("dossier", "dossier.md", lambda: f"# Website dossier\n\nProject: {project_id}\n"),
            ("curie-handoff", "curie-to-website.json", lambda: {"project_id": project_id, "status": "ready"}),
        )
        outcomes = []
        previous: object = base
        for name, output_name, producer in stages:
            outcome = runner.run(
                name,
                {"candidate": base, "previous": previous},
                output_name,
                producer,
            )
            outcomes.append(outcome)
            previous = {"stage": name, "output_hash": outcome.output_hash}
        return AuditResult(
            project_id,
            "review-ready",
            tuple(item.output_path for item in outcomes),
            tuple(item.stage_id for item in outcomes),
            tuple(item.stage_id for item in outcomes if item.reused),
        )
