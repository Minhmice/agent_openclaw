"""Bounded rendering and loading for the legacy Curie review format."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit


class LegacyReviewError(ValueError):
    """Raised when a legacy workflow project cannot become a review card."""


_ISSUE_RE = re.compile(r"^-\s*(P[0-3])\s*:\s*(.+)$")
_URL_RE = re.compile(r"https?://[^\s>]+")
_MAX_MESSAGE_BYTES = 4_000


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LegacyReviewError(f"legacy project {field} is missing")
    return value.strip()


def _bounded(value: str, limit: int = 600) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _section_lines(dossier: str, marker: str) -> list[str]:
    lines = dossier.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    except StopIteration:
        return []
    result: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            if result:
                break
            continue
        if stripped.startswith(("## ", "### ")):
            break
        result.append(stripped)
    return result


def _summary_value(dossier: str) -> str:
    for line in dossier.splitlines():
        if line.strip().startswith("Tóm tắt:"):
            value = line.split(":", 1)[1].strip()
            proof = re.search(r"Proof dày:\s*(.+?)(?:\.\s*Site hiện|$)", value)
            return _bounded(proof.group(1) if proof else value, 360)
    return "Chưa có business proof summary trong dossier."


def _issue_lines(dossier: str) -> list[str]:
    candidates: list[str] = []
    for line in _section_lines(dossier, "Top issues:"):
        match = _ISSUE_RE.match(line)
        if match:
            candidates.append(f"{match.group(1)}  {_bounded(match.group(2), 240)}")
    return candidates[:3]


def _evidence_line(dossier: str) -> str:
    urls: list[str] = []
    for line in _section_lines(dossier, "Evidence:"):
        urls.extend(_URL_RE.findall(line))
    urls = list(dict.fromkeys(urls))
    image_line = next(
        (line.strip() for line in dossier.splitlines() if line.strip().startswith("Ảnh first-party:")),
        None,
    )
    image_text = image_line.split(":", 1)[1].strip() if image_line else "chưa có first-party asset"
    if urls:
        return f"{len(urls)} public URLs · {_bounded(image_text, 180)}"
    return _bounded(image_text, 220)


def _confidence_line(dossier: str) -> str:
    for line in dossier.splitlines():
        if line.strip().startswith("Confidence gaps:"):
            return _bounded(line.split(":", 1)[1].strip(), 360)
    return "Chưa có confidence-gap summary trong dossier."


def load_legacy_review_project(project_dir: Path, project_id: str) -> tuple[dict[str, object], str]:
    """Load one canonical workflow project and its dossier without path escape."""

    if not isinstance(project_dir, Path):
        raise TypeError("project_dir must be a Path")
    identity = _required_text(project_id, "project_id")
    project_path = project_dir / "project.json"
    try:
        project = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise LegacyReviewError("legacy project JSON is unavailable or invalid") from error
    if not isinstance(project, dict) or project.get("project_id") != identity:
        raise LegacyReviewError("legacy project identity is invalid")
    if project.get("status") != "review":
        raise LegacyReviewError("legacy project must be in review state")
    website = _required_text(project.get("website"), "website")
    parsed = urlsplit(website)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LegacyReviewError("legacy project website must be an HTTP(S) URL")
    dossier_name = _required_text(project.get("dossier_file", "curie-dossier.md"), "dossier_file")
    dossier_path = (project_dir / dossier_name).resolve()
    root = project_dir.resolve()
    if dossier_path.parent != root:
        raise LegacyReviewError("legacy dossier path escapes project directory")
    try:
        dossier = dossier_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise LegacyReviewError("legacy dossier is unavailable") from error
    if not dossier.strip():
        raise LegacyReviewError("legacy dossier is empty")
    return dict(project), dossier


def render_legacy_review_message(project: Mapping[str, object], dossier: str) -> str:
    """Render one compact option-A message with no typed-command duplication."""

    if not isinstance(project, Mapping):
        raise TypeError("project must be a mapping")
    if not isinstance(dossier, str) or not dossier.strip():
        raise LegacyReviewError("legacy dossier is empty")
    project_id = _required_text(project.get("project_id"), "project_id")
    name = _required_text(project.get("business_name"), "business_name")
    industry = _required_text(project.get("industry"), "industry")
    website = _required_text(project.get("website"), "website")
    parsed = urlsplit(website)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LegacyReviewError("legacy project website must be an HTTP(S) URL")
    location = "Hà Nội" if "Hà Nội" in dossier or "Hanoi" in dossier else "Quanh Hà Nội"
    issues = _issue_lines(dossier) or ["P2  Chưa có issue ưu tiên được trích xuất từ dossier."]
    message = (
        f"**{name} · Website review**\n"
        f"`{project_id}` · {location} · {_bounded(industry, 180)}\n\n"
        "**WHY NOW**\n"
        f"{_summary_value(dossier)}\n\n"
        "**TOP OPPORTUNITIES**\n"
        + "\n".join(issues)
        + "\n\n**EVIDENCE**\n"
        + _evidence_line(dossier)
        + "\n\n**CONFIDENCE**\n"
        + _confidence_line(dossier)
    )
    if len(message.encode("utf-8")) > _MAX_MESSAGE_BYTES:
        raise LegacyReviewError("compact review message is too large")
    return message
