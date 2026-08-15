"""Strict, bounded loading for deterministic scoring rubrics."""

from __future__ import annotations

import math
import re
from enum import Enum
from pathlib import Path
from typing import Any, TypeAlias

import yaml  # type: ignore[import-untyped]
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from yaml.constructor import ConstructorError  # type: ignore[import-untyped]
from yaml.nodes import MappingNode  # type: ignore[import-untyped]
from yaml.tokens import (  # type: ignore[import-untyped]
    AliasToken,
    AnchorToken,
    DirectiveToken,
    TagToken,
)

MAX_RUBRIC_BYTES = 64 * 1024
MAX_YAML_OBJECTS = 2_048
MAX_YAML_DEPTH = 12
MAX_MAPPING_ITEMS = 128
MAX_SEQUENCE_ITEMS = 256
MAX_STRING_LENGTH = 4_096
MAX_ABS_NUMBER = 1_000_000_000

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_INPUT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,7}$")


class RuleConfigError(ValueError):
    """A rubric is unsafe, structurally invalid, or semantically inconsistent."""


class _StrictSafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    """SafeLoader variant with JSON-like booleans and duplicate-key denial."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "mapping keys must be scalar and hashable",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key: {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


# YAML 1.1 treats yes/no/on/off as booleans. Rubrics accept only JSON/YAML 1.2
# true and false, avoiding environment-dependent scalar interpretation.
_StrictSafeLoader.yaml_implicit_resolvers = {
    key: list(resolvers) for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for _first, _resolvers in list(_StrictSafeLoader.yaml_implicit_resolvers.items()):
    _StrictSafeLoader.yaml_implicit_resolvers[_first] = [
        resolver for resolver in _resolvers if resolver[0] != "tag:yaml.org,2002:bool"
    ]
_StrictSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


class Operator(str, Enum):
    """The complete, deliberately small scoring expression language."""

    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    CONTAINS = "contains"
    MISSING = "missing"
    PRESENT = "present"


Threshold: TypeAlias = str | int | float | bool


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)


class Qualification(_FrozenStrictModel):
    threshold: float = Field(strict=True, allow_inf_nan=False, ge=0, le=100)
    provisional: bool = Field(strict=True)


class Rule(_FrozenStrictModel):
    rule_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN.pattern, strict=True)
    input: str = Field(min_length=1, max_length=128, pattern=_INPUT_PATTERN.pattern, strict=True)
    operator: Operator
    points: float = Field(strict=True, allow_inf_nan=False, ge=0, le=MAX_ABS_NUMBER)
    threshold: Threshold | None = None
    evidence_ids: tuple[
        str,
        ...,
    ] = Field(default_factory=tuple, max_length=64)

    @field_validator("operator", mode="before")
    @classmethod
    def parse_operator(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return Operator(value)
            except ValueError:
                return value
        return value

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def freeze_evidence_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_operator_threshold(self) -> Rule:
        unary = self.operator in {Operator.MISSING, Operator.PRESENT}
        if unary and self.threshold is not None:
            raise ValueError(f"operator {self.operator.value} must not define threshold")
        if not unary and self.threshold is None:
            raise ValueError(f"operator {self.operator.value} requires threshold")
        if isinstance(self.threshold, str) and len(self.threshold) > MAX_STRING_LENGTH:
            raise ValueError("threshold string exceeds limit")
        return self


class CohortOverride(_FrozenStrictModel):
    rules: tuple[Rule, ...] = Field(default_factory=tuple, max_length=256)

    @field_validator("rules", mode="before")
    @classmethod
    def freeze_rules(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class Rubric(_FrozenStrictModel):
    rubric_version: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN.pattern,
        strict=True,
    )
    maximum_score: float = Field(strict=True, allow_inf_nan=False, gt=0, le=100)
    qualification: Qualification
    allowed_inputs: tuple[str, ...] = Field(min_length=1, max_length=256)
    base_rules: tuple[Rule, ...] = Field(max_length=256)
    cohort_overrides: dict[str, CohortOverride] = Field(default_factory=dict)

    @field_validator("allowed_inputs", "base_rules", mode="before")
    @classmethod
    def freeze_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_rubric(self) -> Rubric:
        if len(set(self.allowed_inputs)) != len(self.allowed_inputs):
            raise ValueError("allowed_inputs contains duplicate paths")
        for path in self.allowed_inputs:
            if _INPUT_PATTERN.fullmatch(path) is None:
                raise ValueError(f"invalid allowed input path: {path!r}")
        if len(self.cohort_overrides) > 64:
            raise ValueError("cohort override count exceeds limit")
        rules = list(self.base_rules)
        for cohort_id, override in self.cohort_overrides.items():
            if _ID_PATTERN.fullmatch(cohort_id) is None:
                raise ValueError(f"invalid cohort ID: {cohort_id!r}")
            rules.extend(override.rules)
        rule_ids = [rule.rule_id for rule in rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("rule IDs must be globally unique")
        allowed = set(self.allowed_inputs)
        for rule in rules:
            if rule.input not in allowed:
                raise ValueError(f"rule input {rule.input!r} is not an allowed input")
            if rule.points > self.maximum_score:
                raise ValueError(
                    f"rule {rule.rule_id!r} points exceed maximum score {self.maximum_score}"
                )
        return self


def _scan_yaml_safety(text: str) -> None:
    collection_depth = 0
    object_count = 0
    try:
        for token in yaml.scan(text, Loader=_StrictSafeLoader):
            token_name = type(token).__name__
            if token_name in {
                "BlockMappingStartToken",
                "BlockSequenceStartToken",
                "FlowMappingStartToken",
                "FlowSequenceStartToken",
            }:
                collection_depth += 1
                if collection_depth > MAX_YAML_DEPTH:
                    raise RuleConfigError("YAML nesting depth exceeds limit")
            elif token_name in {
                "BlockEndToken",
                "FlowMappingEndToken",
                "FlowSequenceEndToken",
            }:
                collection_depth -= 1
            elif token_name in {"KeyToken", "ValueToken", "ScalarToken", "BlockEntryToken"}:
                object_count += 1
                if object_count > MAX_YAML_OBJECTS * 3:
                    raise RuleConfigError("YAML token/object count exceeds limit")
            if isinstance(token, AliasToken):
                raise RuleConfigError("YAML aliases are not allowed")
            if isinstance(token, AnchorToken):
                raise RuleConfigError("YAML anchors and aliases are not allowed")
            if isinstance(token, TagToken):
                raise RuleConfigError("YAML custom tags are not allowed")
            if isinstance(token, DirectiveToken):
                raise RuleConfigError("YAML directives are not allowed")
    except RecursionError as exc:
        raise RuleConfigError("YAML nesting depth exceeds limit") from exc
    except yaml.YAMLError as exc:
        raise RuleConfigError(f"invalid YAML syntax: {exc}") from exc


def _validate_bounds(value: Any, *, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_YAML_OBJECTS:
        raise RuleConfigError("YAML object count exceeds limit")
    if depth > MAX_YAML_DEPTH:
        raise RuleConfigError("YAML nesting depth exceeds limit")
    if isinstance(value, dict):
        if len(value) > MAX_MAPPING_ITEMS:
            raise RuleConfigError("YAML mapping item count exceeds limit")
        for key, item in value.items():
            if not isinstance(key, str):
                raise RuleConfigError("YAML mapping keys must be strings")
            _validate_bounds(key, depth=depth + 1, count=count)
            _validate_bounds(item, depth=depth + 1, count=count)
    elif isinstance(value, list):
        if len(value) > MAX_SEQUENCE_ITEMS:
            raise RuleConfigError("YAML sequence item count exceeds limit")
        for item in value:
            _validate_bounds(item, depth=depth + 1, count=count)
    elif isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise RuleConfigError("YAML string length exceeds limit")
    elif isinstance(value, bool | None):
        return
    elif isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            raise RuleConfigError("YAML numeric values must be finite")
        if abs(value) > MAX_ABS_NUMBER:
            raise RuleConfigError("YAML numeric magnitude exceeds limit")
    else:
        raise RuleConfigError(f"unsupported YAML value type: {type(value).__name__}")


def parse_rubric(text: str) -> Rubric:
    """Parse a bounded, versioned rubric from an in-memory YAML document."""

    if not isinstance(text, str):
        raise TypeError("rubric text must be a string")
    if len(text.encode("utf-8")) > MAX_RUBRIC_BYTES:
        raise RuleConfigError("rubric size exceeds limit")
    _scan_yaml_safety(text)
    try:
        raw = yaml.load(text, Loader=_StrictSafeLoader)
    except RecursionError as exc:
        raise RuleConfigError("YAML nesting depth exceeds limit") from exc
    except yaml.YAMLError as exc:
        message = str(exc)
        if "duplicate key" in message:
            raise RuleConfigError(f"duplicate YAML key: {message}") from exc
        raise RuleConfigError(f"invalid YAML: {message}") from exc
    if not isinstance(raw, dict):
        raise RuleConfigError("rubric root must be a YAML mapping")
    _validate_bounds(raw)
    try:
        return Rubric.model_validate(raw)
    except ValidationError as exc:
        message = str(exc)
        if "qualification.provisional" in message or "provisional" in message:
            message = f"qualification provisional must be a YAML boolean true or false: {message}"
        raise RuleConfigError(f"rubric validation failed: {message}") from exc


def load_rubric(path: Path) -> Rubric:
    """Load a rubric without allowing oversized files into the YAML parser."""

    if not isinstance(path, Path):
        raise TypeError("rubric path must be a pathlib.Path")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise RuleConfigError(f"cannot inspect rubric file: {exc}") from exc
    if size > MAX_RUBRIC_BYTES:
        raise RuleConfigError("rubric file size exceeds limit")
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuleConfigError(f"cannot read rubric as UTF-8: {exc}") from exc
    return parse_rubric(text)
