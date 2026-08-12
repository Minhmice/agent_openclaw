from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from openclaw_web.scoring.engine import (
    LeadScore,
    RuleEngine,
    calculate_lead_score,
)
from openclaw_web.scoring.rules import RuleConfigError, load_rubric, parse_rubric

RUBRIC = """
rubric_version: test-v1
maximum_score: 100
qualification:
  threshold: 75
  provisional: true
allowed_inputs:
  - lighthouse.lcp_ms
  - audit.has_contact
  - crawl.tags
base_rules:
  - rule_id: slow-lcp
    input: lighthouse.lcp_ms
    operator: gt
    threshold: 4000
    points: 12
    evidence_ids: [ev-lcp]
  - rule_id: contact-present
    input: audit.has_contact
    operator: eq
    threshold: true
    points: 5
    evidence_ids: [ev-contact]
  - rule_id: conversion-tag
    input: crawl.tags
    operator: contains
    threshold: conversion
    points: 3
    evidence_ids: [ev-tags]
cohort_overrides:
  ecommerce:
    rules:
      - rule_id: checkout-gap
        input: crawl.tags
        operator: contains
        threshold: no-checkout
        points: 7
        evidence_ids: [ev-checkout]
"""


def test_lead_score_uses_approved_weights_and_metadata() -> None:
    result = calculate_lead_score(80, 90, 70, 60)

    assert result == 79.0
    assert isinstance(result, LeadScore)
    assert result.business_score == 80
    assert result.money_score == 90
    assert result.web_ugly_score == 70
    assert result.technical_pain_score == 60
    assert result.qualification_threshold == 75
    assert result.threshold_provisional is True


@pytest.mark.parametrize("value", [-0.01, 100.01, math.inf, -math.inf, math.nan, True, "80"])
def test_lead_score_rejects_values_outside_strict_finite_range(value: object) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        calculate_lead_score(value, 90, 70, 60)  # type: ignore[arg-type]


def test_rule_engine_uses_dotted_lookup_and_exact_cohort_override() -> None:
    engine = RuleEngine(parse_rubric(RUBRIC))
    inputs = {
        "lighthouse": {"lcp_ms": 4100},
        "audit": {"has_contact": True},
        "crawl": {"tags": ["conversion", "no-checkout"]},
    }

    ecommerce = engine.evaluate(inputs, cohort_id="ecommerce")
    generic = engine.evaluate(inputs)

    assert ecommerce.score == 27
    assert ecommerce.matched_rule_ids == (
        "slow-lcp",
        "contact-present",
        "conversion-tag",
        "checkout-gap",
    )
    assert ecommerce.evidence_ids == ("ev-lcp", "ev-contact", "ev-tags", "ev-checkout")
    assert generic.score == 20
    assert "checkout-gap" not in generic.matched_rule_ids


def test_missing_metric_reduces_confidence_and_never_awards_points() -> None:
    result = RuleEngine(parse_rubric(RUBRIC)).evaluate({})

    assert result.score == 0
    assert result.matched_rule_ids == ()
    assert result.unavailable_inputs == (
        "lighthouse.lcp_ms",
        "audit.has_contact",
        "crawl.tags",
    )
    assert result.confidence == 0


def test_rule_result_is_deeply_immutable_typed_and_serializes_deterministically() -> None:
    engine = RuleEngine(parse_rubric(RUBRIC))
    inputs = {
        "lighthouse": {"lcp_ms": 4100},
        "audit": {"has_contact": True},
        "crawl": {"tags": ["conversion"]},
    }

    first = engine.evaluate(inputs)
    second = engine.evaluate(inputs)

    assert first.model_dump_json() == second.model_dump_json()
    with pytest.raises(ValidationError):
        first.score = 0  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.matched_rule_ids[0] = "changed"  # type: ignore[index]


@pytest.mark.parametrize(
    "operator, actual, threshold, expected",
    [
        ("eq", "x", "x", True),
        ("ne", "x", "y", True),
        ("lt", 1, 2, True),
        ("lte", 2, 2, True),
        ("gt", 2, 1, True),
        ("gte", 2, 2, True),
        ("contains", ["x"], "x", True),
        ("missing", None, None, False),
        ("present", 0, None, True),
    ],
)
def test_exact_operator_set(
    operator: str, actual: object, threshold: object, expected: bool
) -> None:
    rubric = f"""
rubric_version: operators-v1
maximum_score: 100
qualification: {{threshold: 75, provisional: true}}
allowed_inputs: [metric]
base_rules:
  - rule_id: check
    input: metric
    operator: {operator}
    points: 1
"""
    if operator not in {"missing", "present"}:
        import yaml

        rendered = yaml.safe_dump(threshold).strip()
        rubric += f"    threshold: {rendered}\n"
    inputs = {} if operator == "missing" else {"metric": actual}
    result = RuleEngine(parse_rubric(rubric)).evaluate(inputs)
    assert (result.score == 1) is expected


def test_operator_comparisons_use_strict_types() -> None:
    rubric = RUBRIC.replace("threshold: true", "threshold: 1")
    with pytest.raises(RuleConfigError, match="type"):
        RuleEngine(parse_rubric(rubric)).evaluate(
            {
                "lighthouse": {"lcp_ms": 4100},
                "audit": {"has_contact": True},
                "crawl": {"tags": []},
            }
        )


@pytest.mark.parametrize(
    "bad_yaml, message",
    [
        (
            RUBRIC.replace("rubric_version: test-v1", "rubric_version: a\nrubric_version: b"),
            "duplicate",
        ),
        (RUBRIC.replace("operator: gt", "operator: regex"), "operator"),
        (RUBRIC.replace("input: lighthouse.lcp_ms", "input: secret.token"), "allowed"),
        (RUBRIC.replace("points: 12", "points: 101"), "maximum"),
        (RUBRIC + "unknown_field: nope\n", "extra"),
        (RUBRIC.replace("[ev-lcp]", "&ids [ev-lcp]").replace("[ev-contact]", "*ids"), "alias"),
        (RUBRIC.replace("test-v1", "!unsafe value"), "tag"),
        (RUBRIC.replace("provisional: true", "provisional: yes"), "boolean"),
    ],
)
def test_rubric_rejects_adversarial_yaml(bad_yaml: str, message: str) -> None:
    with pytest.raises(RuleConfigError, match=message):
        parse_rubric(bad_yaml)


def test_rubric_loader_enforces_file_and_structure_bounds(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.yaml"
    oversized.write_text("#" * 70_000, encoding="utf-8")
    with pytest.raises(RuleConfigError, match="size"):
        load_rubric(oversized)

    deeply_nested = RUBRIC.replace("threshold: 4000", "threshold: [[[[[[[[[[[[[1]]]]]]]]]]]]]")
    with pytest.raises(RuleConfigError, match="depth"):
        parse_rubric(deeply_nested)


def test_rubric_rejects_extreme_depth_before_yaml_construction() -> None:
    extreme = "rubric_version: bomb-v1\nvalue: " + ("[" * 1_000) + "1" + ("]" * 1_000)
    with pytest.raises(RuleConfigError, match="depth"):
        parse_rubric(extreme)


def test_strict_rubric_boolean_policy_does_not_mutate_pyyaml_globally() -> None:
    import yaml

    assert yaml.safe_load("value: yes") == {"value": True}


def test_unknown_input_payload_path_and_unknown_cohort_are_rejected() -> None:
    engine = RuleEngine(parse_rubric(RUBRIC))
    valid = {
        "lighthouse": {"lcp_ms": 4100},
        "audit": {"has_contact": True},
        "crawl": {"tags": []},
    }
    with pytest.raises(ValueError, match="unknown input path"):
        engine.evaluate({**valid, "secret": {"token": "nope"}})
    with pytest.raises(ValueError, match="unknown input path"):
        engine.evaluate({**valid, "secret": {}})
    partial = engine.evaluate({"lighthouse": {}})
    assert "lighthouse.lcp_ms" in partial.unavailable_inputs
    with pytest.raises(ValueError, match="unknown cohort"):
        engine.evaluate(valid, cohort_id="made-up")


def test_score_overflow_is_rejected_at_evaluation() -> None:
    rubric = RUBRIC.replace("points: 12", "points: 96")
    engine = RuleEngine(parse_rubric(rubric))
    with pytest.raises(ValueError, match="overflow"):
        engine.evaluate(
            {
                "lighthouse": {"lcp_ms": 4100},
                "audit": {"has_contact": True},
                "crawl": {"tags": ["conversion"]},
            }
        )
