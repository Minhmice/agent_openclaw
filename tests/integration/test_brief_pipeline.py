from __future__ import annotations

import pytest

from openclaw_web.brief.generator import BriefPipeline


@pytest.mark.asyncio
async def test_approved_lead_generates_required_brief_artifacts(tmp_path) -> None:
    pipeline = BriefPipeline(tmp_path)
    result = await pipeline.generate(
        {
            "project_id": "demo-project",
            "approval": {"approved": True, "approved_by": "620891893659598850"},
            "website_url": "https://example.com/",
            "business_name": "Demo Company",
        }
    )

    assert result.valid
    assert set(result.artifacts) >= {
        "website-analysis.json",
        "content-inventory.json",
        "visual-inventory.json",
        "design-audit.json",
        "competitive-positioning.json",
        "redesign-brief.json",
        "visual-worlds.json",
        "design-genome.json",
        "design-tokens.json",
        "component-system.json",
        "page-blueprints.json",
        "DESIGN.md",
    }
    assert all((tmp_path / "demo-project" / artifact).exists() for artifact in result.artifacts)
