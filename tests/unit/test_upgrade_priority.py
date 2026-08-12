from __future__ import annotations

import math

import pytest

from openclaw_web.scoring.priority import calculate_upgrade_priority, rank_issues


def test_upgrade_priority_uses_approved_formula() -> None:
    assert calculate_upgrade_priority(10, 8, 6, 4, 2, 0) == 6.3


def test_p0_always_precedes_higher_numeric_p1() -> None:
    issues = [
        {"issue_id": "p1", "severity": "P1", "priority": 99},
        {"issue_id": "p0", "severity": "P0", "priority": 60},
    ]
    assert [item["issue_id"] for item in rank_issues(issues)] == ["p0", "p1"]


def test_rank_uses_formula_then_documented_stable_tie_breaks() -> None:
    issues = [
        {
            "issue_id": "z",
            "severity": "P1",
            "business_impact": 5,
            "funnel_proximity": 8,
            "evidence_confidence": 7,
            "reach_frequency": 5,
            "dependency_unlock": 4,
            "effort_efficiency": 3,
        },
        {
            "issue_id": "a",
            "severity": "P1",
            "business_impact": 5,
            "funnel_proximity": 8,
            "evidence_confidence": 7,
            "reach_frequency": 5,
            "dependency_unlock": 4,
            "effort_efficiency": 3,
        },
        {
            "issue_id": "higher",
            "severity": "P1",
            "business_impact": 8,
            "funnel_proximity": 8,
            "evidence_confidence": 7,
            "reach_frequency": 5,
            "dependency_unlock": 4,
            "effort_efficiency": 3,
        },
    ]
    ranked = rank_issues(issues)
    assert [item["issue_id"] for item in ranked] == ["higher", "a", "z"]
    assert "priority" not in issues[0]
    assert ranked[1]["priority"] == calculate_upgrade_priority(5, 8, 7, 5, 4, 3)


@pytest.mark.parametrize(
    "issue",
    [
        {"issue_id": "missing-severity", "priority": 2},
        {"issue_id": "bad-severity", "severity": "critical", "priority": 2},
        {"issue_id": "bool", "severity": "P1", "priority": True},
        {"issue_id": "nan", "severity": "P1", "priority": math.nan},
        {"issue_id": "range", "severity": "P1", "priority": 101},
        {
            "issue_id": "partial-formula",
            "severity": "P1",
            "business_impact": 5,
            "funnel_proximity": 5,
        },
    ],
)
def test_rank_rejects_malformed_issues(issue: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        rank_issues([issue])


def test_rank_rejects_duplicate_issue_ids() -> None:
    issues = [
        {"issue_id": "same", "severity": "P1", "priority": 2},
        {"issue_id": "same", "severity": "P2", "priority": 1},
    ]
    with pytest.raises(ValueError, match="duplicate issue_id"):
        rank_issues(issues)


@pytest.mark.parametrize("value", [-0.01, 10.01, math.inf, math.nan, True, "5"])
def test_upgrade_dimensions_are_strict_finite_zero_to_ten(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        calculate_upgrade_priority(value, 5, 5, 5, 5, 5)  # type: ignore[arg-type]
