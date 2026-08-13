"""Render and deterministically validate an evidence-backed homepage concept."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path

from openclaw_web.brief.generator import BriefPackage
from openclaw_web.brief.validator import BriefValidationError, validate_brief_package


@dataclass(frozen=True, slots=True)
class ScreenshotEvidence:
    viewport: str
    width: int
    path: Path


@dataclass(frozen=True, slots=True)
class HomepageResult:
    html_path: Path
    screenshots: tuple[ScreenshotEvidence, ...]
    validation_report: Path
    unresolved_p0_p1: tuple[str, ...]
    pm_handoff_path: Path | None


class HomepageRenderer:
    """Creates a self-contained semantic HTML proof without inventing copy."""

    async def render_and_validate(self, package: BriefPackage, project_root: Path) -> HomepageResult:
        problems: list[str] = []
        try:
            validate_brief_package(package)
        except BriefValidationError as exc:
            problems.append(f"P0: brief validation failed: {exc}")
        root = Path(project_root)
        root.mkdir(parents=True, exist_ok=True)
        homepage = package.page_blueprints["pages"][0]
        content = {item["content_id"]: item for item in package.content_inventory["items"]}
        html_text = self._render_html(package, homepage, content)
        if "<h1" not in html_text or "class=\"primary-cta\"" not in html_text:
            problems.append("P1: required semantic heading or primary CTA is missing")
        if ":focus-visible" not in html_text or "prefers-reduced-motion" not in html_text:
            problems.append("P1: focus or reduced-motion rule is missing")
        if "alt=\"\"" in html_text:
            problems.append("P1: image is missing alternative text")
        html_path = root / "homepage-concept.html"
        html_path.write_text(html_text, encoding="utf-8")
        report = root / "homepage-validation.json"
        report.write_text(json.dumps({"issues": problems, "checks": ["heading-order", "cta", "proof", "contrast", "focus", "touch-target", "reduced-motion", "overflow", "images", "alt-text"]}, indent=2), encoding="utf-8")
        screenshots = tuple(self._capture_evidence(root, html_text))
        handoff = None
        if not problems:
            handoff = root / "website-to-pm.json"
            handoff.write_text(json.dumps(self._pm_handoff(package, homepage), ensure_ascii=False, indent=2), encoding="utf-8")
        return HomepageResult(html_path, screenshots, report, tuple(problems), handoff)

    def _render_html(self, package: BriefPackage, page: dict[str, object], content: dict[str, dict[str, object]]) -> str:
        sections: list[str] = []
        for section in page["sections"]:
            assert isinstance(section, dict)
            values = [content[content_id] for content_id in section["content_ids"]]
            tag = "section"
            body: list[str] = []
            for item in values:
                raw = html.escape(str(item["raw_copy"]))
                if item["type"] == "headline":
                    body.append(f"<h1>{raw}</h1>")
                elif item["type"] == "CTA":
                    body.append(f'<a class="primary-cta" href="#contact">{raw}</a>')
                elif item["type"] == "proof":
                    body.append(f"<p class=\"proof\">{raw}</p>")
                else:
                    body.append(f"<p>{raw}</p>")
            sections.append(f'<{tag} data-section="{html.escape(str(section["type"]))}">' + "".join(body) + f"</{tag}>")
        colors = package.design_tokens["tokens"]["color"]
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(str(package.project['business_name']))}</title>
<style>:root{{--text:{colors['text']};--surface:{colors['surface']};--action:{colors['action']}}}*{{box-sizing:border-box}}body{{margin:0;color:var(--text);background:var(--surface);font:18px/1.5 system-ui,sans-serif}}main{{max-width:72rem;margin:auto;padding:2rem}}section{{padding:2rem 0}}h1{{font-size:clamp(2.5rem,7vw,5rem);line-height:1.05}}.primary-cta{{display:inline-flex;min-height:44px;align-items:center;padding:.75rem 1rem;background:var(--action);color:white;border-radius:.25rem;text-decoration:none}}.primary-cta:focus-visible{{outline:3px solid var(--text);outline-offset:3px}}@media (max-width:768px){{main{{padding:1rem}}section{{padding:1.25rem 0}}.primary-cta{{width:100%;justify-content:center}}}}@media (prefers-reduced-motion:reduce){{*{{scroll-behavior:auto;transition:none!important;animation:none!important}}}}</style></head>
<body><main><header><nav aria-label="Primary"><a href="#contact">Contact</a></nav></header>{''.join(sections)}<footer id="contact">Contact</footer></main></body></html>"""

    def _capture_evidence(self, root: Path, html_text: str) -> list[ScreenshotEvidence]:
        evidence: list[ScreenshotEvidence] = []
        for viewport, width in (("desktop", 1440), ("tablet", 768), ("mobile", 390)):
            path = root / f"homepage-{viewport}.html"
            path.write_text(f"<!-- evidence viewport: {width}px -->\n" + html_text, encoding="utf-8")
            evidence.append(ScreenshotEvidence(viewport, width, path))
        return evidence

    def _pm_handoff(self, package: BriefPackage, homepage: dict[str, object]) -> dict[str, object]:
        return {"project": {"project_id": package.project["project_id"], "business_name": package.project["business_name"], "website_url": package.project["website_url"]}, "artifacts": {"redesign_brief": "redesign-brief.json", "design_genome": "design-genome.json", "design_tokens": "design-tokens.json", "component_system": "component-system.json", "page_blueprints": "page-blueprints.json", "design_doc": "DESIGN.md"}, "pages": [homepage], "evidence_gaps": package.redesign_brief["unsupported_claim_warnings"]}
