"""Deterministic lead formulas and evidence-backed rule evaluation."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import NoReturn, Self

from pydantic import BaseModel, ConfigDict, Field

from openclaw_web.scoring.rules import Operator, Rubric, Rule, RuleConfigError

LEAD_SCORE_WEIGHTS = {
    "business_score": 0.30,
    "money_score": 0.35,
    "web_ugly_score": 0.25,
    "technical_pain_score": 0.10,
}
QUALIFICATION_THRESHOLD = 75.0

_MISSING = object()


class LeadScore(float):
    """Immutable numeric lead score carrying its reproducibility metadata."""

    def __new__(
        cls,
        value: float,
        *,
        business_score: float,
        money_score: float,
        web_ugly_score: float,
        technical_pain_score: float,
    ) -> Self:
        instance = super().__new__(cls, value)
        object.__setattr__(instance, "business_score", business_score)
        object.__setattr__(instance, "money_score", money_score)
        object.__setattr__(instance, "web_ugly_score", web_ugly_score)
        object.__setattr__(instance, "technical_pain_score", technical_pain_score)
        object.__setattr__(instance, "qualification_threshold", QUALIFICATION_THRESHOLD)
        object.__setattr__(instance, "threshold_provisional", True)
        object.__setattr__(instance, "weights", tuple(LEAD_SCORE_WEIGHTS.items()))
        object.__setattr__(instance, "_frozen", True)
        return instance

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise TypeError("LeadScore is immutable")


class RuleResult(BaseModel):
    """Canonical output for one deterministic rubric evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    score: float = Field(strict=True, allow_inf_nan=False, ge=0, le=100)
    matched_rule_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    unavailable_inputs: tuple[str, ...]
    confidence: float = Field(strict=True, allow_inf_nan=False, ge=0, le=1)
    rubric_version: str
    cohort_id: str | None
    qualification_threshold: float = Field(strict=True, allow_inf_nan=False, ge=0, le=100)
    threshold_provisional: bool
    qualified: bool


def _strict_finite_number(value: object, *, name: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a strict number")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= maximum:
        raise ValueError(f"{name} must be finite and between 0 and {maximum:g}")
    return number


def calculate_lead_score(
    business_score: float,
    money_score: float,
    web_ugly_score: float,
    technical_pain_score: float,
) -> LeadScore:
    """Calculate Business 30% + Money 35% + WebUgly 25% + TechnicalPain 10%."""

    values = {
        "business_score": _strict_finite_number(business_score, name="business_score", maximum=100),
        "money_score": _strict_finite_number(money_score, name="money_score", maximum=100),
        "web_ugly_score": _strict_finite_number(web_ugly_score, name="web_ugly_score", maximum=100),
        "technical_pain_score": _strict_finite_number(
            technical_pain_score, name="technical_pain_score", maximum=100
        ),
    }
    value = round(sum(values[name] * weight for name, weight in LEAD_SCORE_WEIGHTS.items()), 2)
    return LeadScore(value, **values)


def _lookup(inputs: Mapping[str, object], path: str) -> object:
    current: object = inputs
    for component in path.split("."):
        if not isinstance(current, Mapping) or component not in current:
            return _MISSING
        current = current[component]
    return current


def _leaf_paths(value: object, prefix: str = "") -> set[str]:
    if isinstance(value, Mapping):
        if prefix and not value:
            return {prefix}
        paths: set[str] = set()
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("input mapping keys must be non-empty strings")
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(item, Mapping):
                paths.update(_leaf_paths(item, path))
            else:
                paths.add(path)
        return paths
    raise TypeError("scoring inputs must be a mapping")


def _strict_equal(actual: object, threshold: object) -> bool:
    if type(actual) is not type(threshold):
        raise RuleConfigError("equality comparison operands must have the same strict type")
    return actual == threshold


def _ordered_compare(actual: object, threshold: object, operator: Operator) -> bool:
    if isinstance(actual, bool) or isinstance(threshold, bool):
        raise RuleConfigError("ordered comparison operands must have compatible strict types")
    if isinstance(actual, int | float) and isinstance(threshold, int | float):
        left_number = float(actual)
        right_number = float(threshold)
        if not math.isfinite(left_number) or not math.isfinite(right_number):
            raise RuleConfigError("numeric comparison operands must be finite")
        if operator is Operator.LT:
            return left_number < right_number
        if operator is Operator.LTE:
            return left_number <= right_number
        if operator is Operator.GT:
            return left_number > right_number
        return left_number >= right_number
    elif type(actual) is type(threshold) and isinstance(actual, str):
        right_string = str(threshold)
        if operator is Operator.LT:
            return actual < right_string
        if operator is Operator.LTE:
            return actual <= right_string
        if operator is Operator.GT:
            return actual > right_string
        return actual >= right_string
    else:
        raise RuleConfigError("ordered comparison operands must have compatible strict types")


def _matches(rule: Rule, actual: object) -> bool:
    if rule.operator is Operator.MISSING:
        # An unavailable path is tracked as a confidence gap and never awards
        # points. ``missing`` therefore means an explicitly observed null.
        return actual is None
    if rule.operator is Operator.PRESENT:
        return actual is not _MISSING
    if actual is _MISSING:
        return False
    threshold = rule.threshold
    if rule.operator is Operator.EQ:
        return _strict_equal(actual, threshold)
    if rule.operator is Operator.NE:
        return not _strict_equal(actual, threshold)
    if rule.operator in {Operator.LT, Operator.LTE, Operator.GT, Operator.GTE}:
        return _ordered_compare(actual, threshold, rule.operator)
    if rule.operator is Operator.CONTAINS:
        if isinstance(actual, str):
            if not isinstance(threshold, str):
                raise RuleConfigError("contains on a string requires a string threshold type")
            return threshold in actual
        if isinstance(actual, Sequence) and not isinstance(actual, bytes | bytearray):
            return any(type(item) is type(threshold) and item == threshold for item in actual)
        raise RuleConfigError("contains input must be a string or sequence")
    raise AssertionError(f"unhandled operator {rule.operator}")


class RuleEngine:
    """Evaluate a validated base rubric followed by one exact cohort override."""

    def __init__(self, rubric: Rubric) -> None:
        if not isinstance(rubric, Rubric):
            raise TypeError("rubric must be a validated Rubric")
        self._rubric = rubric

    def evaluate(self, inputs: Mapping[str, object], *, cohort_id: str | None = None) -> RuleResult:
        if not isinstance(inputs, Mapping):
            raise TypeError("scoring inputs must be a mapping")
        if cohort_id is not None and cohort_id not in self._rubric.cohort_overrides:
            raise ValueError(f"unknown cohort: {cohort_id!r}")
        allowed = set(self._rubric.allowed_inputs)
        allowed_prefixes = {
            ".".join(components[:index])
            for path in allowed
            for components in [path.split(".")]
            for index in range(1, len(components))
        }
        unknown = sorted(
            path for path in _leaf_paths(inputs) if path not in allowed | allowed_prefixes
        )
        if unknown:
            raise ValueError(f"unknown input path(s): {', '.join(unknown)}")

        rules = list(self._rubric.base_rules)
        if cohort_id is not None:
            rules.extend(self._rubric.cohort_overrides[cohort_id].rules)
        matched: list[str] = []
        evidence: list[str] = []
        unavailable: list[str] = []
        score = 0.0
        for rule in rules:
            actual = _lookup(inputs, rule.input)
            if actual is _MISSING and rule.input not in unavailable:
                unavailable.append(rule.input)
            if _matches(rule, actual):
                matched.append(rule.rule_id)
                score += rule.points
                for evidence_id in rule.evidence_ids:
                    if evidence_id not in evidence:
                        evidence.append(evidence_id)
        if score > self._rubric.maximum_score:
            raise ValueError(
                f"score overflow: {score:g} exceeds maximum {self._rubric.maximum_score:g}"
            )
        assessed = len({rule.input for rule in rules})
        confidence = 1.0 if assessed == 0 else (assessed - len(unavailable)) / assessed
        threshold = self._rubric.qualification.threshold
        return RuleResult(
            score=float(round(score, 6)),
            matched_rule_ids=tuple(matched),
            evidence_ids=tuple(evidence),
            unavailable_inputs=tuple(unavailable),
            confidence=float(round(confidence, 6)),
            rubric_version=self._rubric.rubric_version,
            cohort_id=cohort_id,
            qualification_threshold=threshold,
            threshold_provisional=self._rubric.qualification.provisional,
            qualified=score >= threshold,
        )
