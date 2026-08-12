"""Strict UpgradePriority calculation and deterministic issue ordering."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TypeAlias

UPGRADE_PRIORITY_WEIGHTS = {
    "business_impact": 0.30,
    "funnel_proximity": 0.20,
    "evidence_confidence": 0.15,
    "reach_frequency": 0.15,
    "dependency_unlock": 0.10,
    "effort_efficiency": 0.10,
}
SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
IssueValue: TypeAlias = str | int | float | bool | None


def _bounded_number(value: object, *, name: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a strict number")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= maximum:
        raise ValueError(f"{name} must be finite and between 0 and {maximum:g}")
    return result


def calculate_upgrade_priority(
    business_impact: float,
    funnel_proximity: float,
    evidence_confidence: float,
    reach_frequency: float,
    dependency_unlock: float,
    effort_efficiency: float,
) -> float:
    """Apply the approved 30/20/15/15/10/10 formula to strict 0–10 inputs."""

    raw = {
        "business_impact": business_impact,
        "funnel_proximity": funnel_proximity,
        "evidence_confidence": evidence_confidence,
        "reach_frequency": reach_frequency,
        "dependency_unlock": dependency_unlock,
        "effort_efficiency": effort_efficiency,
    }
    values = {name: _bounded_number(value, name=name, maximum=10) for name, value in raw.items()}
    return round(sum(values[name] * weight for name, weight in UPGRADE_PRIORITY_WEIGHTS.items()), 2)


def rank_issues(
    issues: Sequence[Mapping[str, IssueValue]],
) -> list[dict[str, IssueValue]]:
    """Return copies ordered by severity, formula, funnel, confidence, and issue ID."""

    if isinstance(issues, str | bytes | bytearray) or not isinstance(issues, Sequence):
        raise TypeError("issues must be a sequence of mappings")
    ranked: list[dict[str, IssueValue]] = []
    dimensions = set(UPGRADE_PRIORITY_WEIGHTS)
    issue_ids: set[str] = set()
    for issue in issues:
        if not isinstance(issue, Mapping):
            raise TypeError("each issue must be a mapping")
        item = dict(issue)
        issue_id = item.get("issue_id")
        severity = item.get("severity")
        if not isinstance(issue_id, str) or not issue_id.strip():
            raise ValueError("issue_id must be a non-empty string")
        if issue_id in issue_ids:
            raise ValueError(f"duplicate issue_id: {issue_id!r}")
        issue_ids.add(issue_id)
        if not isinstance(severity, str) or severity not in SEVERITY_RANK:
            raise ValueError("severity must be exactly P0, P1, P2, or P3")
        supplied = dimensions.intersection(item)
        if supplied and supplied != dimensions:
            missing = sorted(dimensions - supplied)
            raise ValueError(f"formula issue is missing dimensions: {', '.join(missing)}")
        if supplied:
            priority = calculate_upgrade_priority(
                *[item[name] for name in UPGRADE_PRIORITY_WEIGHTS]  # type: ignore[arg-type]
            )
            item["priority"] = priority
            funnel = _bounded_number(item["funnel_proximity"], name="funnel_proximity", maximum=10)
            confidence = _bounded_number(
                item["evidence_confidence"], name="evidence_confidence", maximum=10
            )
        else:
            if "priority" not in item:
                raise ValueError("issue must provide priority or all UpgradePriority dimensions")
            priority = _bounded_number(item["priority"], name="priority", maximum=100)
            item["priority"] = priority
            funnel = 0.0
            confidence = 0.0
        item["_sort_severity"] = SEVERITY_RANK[severity]
        item["_sort_funnel"] = funnel
        item["_sort_confidence"] = confidence
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            item["_sort_severity"],
            -float(item["priority"]),  # type: ignore[arg-type]
            -float(item["_sort_funnel"]),  # type: ignore[arg-type]
            -float(item["_sort_confidence"]),  # type: ignore[arg-type]
            str(item["issue_id"]),
        )
    )
    for item in ranked:
        del item["_sort_severity"]
        del item["_sort_funnel"]
        del item["_sort_confidence"]
    return ranked
