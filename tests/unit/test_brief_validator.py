from __future__ import annotations

import pytest

from openclaw_web.brief.generator import BriefPackage
from openclaw_web.brief.validator import BriefValidationError, validate_brief_package


def test_page_blueprint_rejects_missing_content_reference() -> None:
    package = BriefPackage.fixture("demo-project")
    package.page_blueprints["pages"][0]["sections"][0]["content_ids"] = ["missing"]

    with pytest.raises(BriefValidationError, match="content"):
        validate_brief_package(package)


def test_interactive_component_requires_states() -> None:
    package = BriefPackage.fixture("demo-project")
    package.component_system["components"]["primary-cta"].pop("states")

    with pytest.raises(BriefValidationError, match="states"):
        validate_brief_package(package)
