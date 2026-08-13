"""Cross-artifact integrity checks for a Website Brief package."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class BriefValidationError(ValueError):
    """Raised when a package cannot safely progress to the PM workflow."""


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BriefValidationError(f"{name} must be an object")
    return value


def validate_brief_package(package: Any) -> None:
    """Validate references needed to render and hand off a truthful brief."""

    content = _require_mapping(package.content_inventory, "content inventory")
    content_ids = {
        item.get("content_id")
        for item in content.get("items", [])
        if isinstance(item, Mapping) and isinstance(item.get("content_id"), str)
    }
    components = _require_mapping(package.component_system, "component system").get("components")
    components = _require_mapping(components, "components")
    tokens = _require_mapping(package.design_tokens, "design tokens").get("tokens")
    tokens = _require_mapping(tokens, "tokens")
    genome = _require_mapping(package.design_genome, "design genome")
    if not genome.get("design_thesis"):
        raise BriefValidationError("design genome is missing a design thesis")
    if not tokens.get("color") or not tokens.get("space"):
        raise BriefValidationError("design tokens must provide color and space")

    blueprints = _require_mapping(package.page_blueprints, "page blueprints").get("pages")
    if not isinstance(blueprints, list) or not blueprints:
        raise BriefValidationError("page blueprints must contain at least one page")
    for page in blueprints:
        page = _require_mapping(page, "page blueprint")
        for field in ("role", "audience", "conversion_intent", "primary_cta", "responsive_notes"):
            if not page.get(field):
                raise BriefValidationError(f"page blueprint is missing {field}")
        seen_orders: set[int] = set()
        for section in page.get("sections", []):
            section = _require_mapping(section, "page section")
            order = section.get("order")
            if not isinstance(order, int) or order in seen_orders:
                raise BriefValidationError("page blueprint has duplicate or invalid section order")
            seen_orders.add(order)
            for content_id in section.get("content_ids", []):
                if content_id not in content_ids:
                    raise BriefValidationError(f"section references unknown content: {content_id}")
            component_id = section.get("component")
            if component_id not in components:
                raise BriefValidationError(f"section references unknown component: {component_id}")
            if not section.get("responsive_notes"):
                raise BriefValidationError("section is missing responsive notes")
    for component_id, component in components.items():
        component = _require_mapping(component, f"component {component_id}")
        if component.get("interactive") and not component.get("states"):
            raise BriefValidationError(f"interactive component {component_id} is missing states")
