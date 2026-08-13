from __future__ import annotations

import json
from pathlib import Path

import pytest

from openclaw_web.pipeline.audit import AuditPipeline


@pytest.mark.asyncio
async def test_pipeline_creates_review_ready_artifacts(tmp_path: Path) -> None:
    pipeline = AuditPipeline(tmp_path)
    result = await pipeline.audit({"project_id": "project-1", "name": "Demo", "url": "https://example.com/"})

    assert result.state == "review-ready"
    assert set(result.artifacts) >= {
        "candidate.json", "evidence.json", "pages.json", "scores.json",
        "issues.json", "dossier.md", "curie-to-website.json",
    }
    assert all((tmp_path / result.project_id / name).exists() for name in result.artifacts)


@pytest.mark.asyncio
async def test_completed_stages_are_reused_for_matching_input(tmp_path: Path) -> None:
    pipeline = AuditPipeline(tmp_path)
    candidate = {"project_id": "project-2", "name": "Demo", "url": "https://example.com/"}
    first = await pipeline.audit(candidate)
    second = await pipeline.audit(candidate)

    assert second.reused_stage_ids == first.completed_stage_ids
    checkpoint = json.loads((tmp_path / "project-2" / "run.json").read_text(encoding="utf-8"))
    assert all(stage["checkpoint"]["input_hash"] for stage in checkpoint["stages"])


@pytest.mark.asyncio
async def test_changed_input_does_not_reuse_completed_stage(tmp_path: Path) -> None:
    pipeline = AuditPipeline(tmp_path)
    await pipeline.audit({"project_id": "project-3", "name": "Old", "url": "https://example.com/"})
    result = await pipeline.audit({"project_id": "project-3", "name": "New", "url": "https://example.com/"})

    assert result.reused_stage_ids == ()
