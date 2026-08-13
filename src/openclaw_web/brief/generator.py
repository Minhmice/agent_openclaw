"""Deterministic local Website Brief generation from an approved handoff."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw_web.brief.stages import REQUIRED_ARTIFACTS
from openclaw_web.brief.validator import validate_brief_package


@dataclass(slots=True)
class BriefPackage:
    project: dict[str, Any]
    website_analysis: dict[str, Any]
    content_inventory: dict[str, Any]
    visual_inventory: dict[str, Any]
    design_audit: dict[str, Any]
    competitive_positioning: dict[str, Any]
    redesign_brief: dict[str, Any]
    visual_worlds: dict[str, Any]
    design_genome: dict[str, Any]
    design_tokens: dict[str, Any]
    component_system: dict[str, Any]
    page_blueprints: dict[str, Any]

    @classmethod
    def fixture(cls, project_id: str) -> BriefPackage:
        project = {"project_id": project_id, "business_name": "Demo Company", "website_url": "https://example.com/"}
        content = {"items": [
            {"content_id": "hero-title", "type": "headline", "raw_copy": "Demo Company", "claim_status": "observed"},
            {"content_id": "hero-copy", "type": "paragraph", "raw_copy": "A clear, evidence-backed service offer.", "claim_status": "observed"},
            {"content_id": "hero-cta", "type": "CTA", "raw_copy": "Request a consultation", "claim_status": "observed"},
            {"content_id": "proof-1", "type": "proof", "raw_copy": "Public business evidence", "claim_status": "observed"},
        ]}
        components = {"components": {
            "primary-cta": {"interactive": True, "states": ["default", "hover", "focus"], "responsive_behavior": "full-width on mobile"},
            "proof-band": {"interactive": False, "states": [], "responsive_behavior": "stack on mobile"},
        }}
        blueprints = {"pages": [{"slug": "homepage", "role": "conversion", "audience": "prospective customers", "conversion_intent": "request consultation", "primary_cta": "hero-cta", "responsive_notes": "Stack content and retain CTA prominence.", "sections": [
            {"order": 1, "type": "hero", "component": "primary-cta", "content_ids": ["hero-title", "hero-copy", "hero-cta"], "proof_ids": ["proof-1"], "responsive_notes": "CTA remains visible above the fold."},
            {"order": 2, "type": "proof", "component": "proof-band", "content_ids": ["proof-1"], "proof_ids": ["proof-1"], "responsive_notes": "One column below 768px."},
        ]}]}
        return cls(project, {"business": project, "pages": [{"url": project["website_url"]}]}, content, {"colors": []}, {"findings": []}, {"opportunities": []}, {"mode": "refresh", "unsupported_claim_warnings": []}, {"worlds": [{"name": "Clear Trust", "score": 1.0}]}, {"design_thesis": "Make the business clear, credible, and easy to contact.", "responsive_rules": "Intentional desktop, tablet, and mobile layouts."}, {"tokens": {"color": {"text": "#172033", "surface": "#ffffff", "action": "#0759d6"}, "space": {"4": "1rem", "8": "2rem"}, "breakpoint": {"tablet": "768px"}}}, components, blueprints)

    def artifacts(self) -> dict[str, object]:
        return {"website-analysis.json": self.website_analysis, "content-inventory.json": self.content_inventory, "visual-inventory.json": self.visual_inventory, "design-audit.json": self.design_audit, "competitive-positioning.json": self.competitive_positioning, "redesign-brief.json": self.redesign_brief, "visual-worlds.json": self.visual_worlds, "design-genome.json": self.design_genome, "design-tokens.json": self.design_tokens, "component-system.json": self.component_system, "page-blueprints.json": self.page_blueprints, "DESIGN.md": "# Design System\n\n## Design Thesis\n\n" + self.design_genome["design_thesis"] + "\n"}


@dataclass(frozen=True, slots=True)
class BriefResult:
    project_id: str
    artifacts: tuple[str, ...]
    valid: bool


class BriefPipeline:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)

    async def generate(self, approved_project: dict[str, Any]) -> BriefResult:
        approval = approved_project.get("approval")
        if not isinstance(approval, dict) or approval.get("approved") is not True:
            raise ValueError("Website Brief requires an approved Curie handoff")
        project_id = approved_project.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("approved project requires project_id")
        package = BriefPackage.fixture(project_id)
        package.project.update({key: value for key, value in approved_project.items() if key in {"business_name", "website_url"}})
        package.website_analysis["business"] = package.project
        validate_brief_package(package)
        output = self.project_root / project_id
        output.mkdir(parents=True, exist_ok=True)
        for name, document in package.artifacts().items():
            path = output / name
            if name.endswith(".json"):
                path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            else:
                path.write_text(str(document), encoding="utf-8")
        return BriefResult(project_id, REQUIRED_ARTIFACTS, True)
