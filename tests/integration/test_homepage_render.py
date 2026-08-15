from __future__ import annotations

import pytest

from openclaw_web.brief.generator import BriefPackage
from openclaw_web.render.homepage import HomepageRenderer


@pytest.mark.asyncio
async def test_homepage_concept_renders_and_allows_clean_pm_handoff(tmp_path) -> None:
    renderer = HomepageRenderer()
    result = await renderer.render_and_validate(BriefPackage.fixture("demo-project"), tmp_path)

    assert result.html_path.exists()
    assert {shot.viewport for shot in result.screenshots} == {"desktop", "tablet", "mobile"}
    assert not result.unresolved_p0_p1
    assert result.validation_report.exists()
    assert result.pm_handoff_path is not None
    assert result.pm_handoff_path.exists()
