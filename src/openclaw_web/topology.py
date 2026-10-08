"""Strict, role-scoped topology contracts for Lead Intelligence.

The topology is deliberately kept separate from OpenClaw's physical agent
configuration.  It is the canonical logical map used by the local
orchestrator; a later adapter may render only the fields supported by the
live OpenClaw version.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ConfigDict, Field, field_validator, model_validator

from openclaw_web.models import NonBlankString, StrictModel

CANONICAL_STAGE_FLOW: tuple[str, ...] = (
    "define_market",
    "discover",
    "resolve_entities",
    "cheap_filter",
    "business_fit",
    "agency_fit",
    "digital_gap",
    "deep_audit",
    "commercial_opportunity",
    "dealability",
    "evidence_verification",
    "red_team",
    "score_survivors",
    "rank",
    "portfolio_selection",
    "human_approval",
    "redesign_intelligence",
)

REDESIGN_STAGE_FLOW: tuple[str, ...] = (
    "business_truth",
    "content_inventory",
    "visual_dna",
    "keep_evolve_retire",
    "redesign_mode",
    "design_direction",
    "prototype",
    "design_system",
    "page_blueprints",
)

_ROLE_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_SECRET_NAME = re.compile(
    r"^(?:[A-Z][A-Z0-9_]*_(?:TOKEN|PASSWORD|API_KEY|SECRET|PRIVATE_KEY)|"
    r"OPENCLAW_[A-Z0-9_]+)$"
)
_SECRET_KEY = re.compile(r"(?:api[_-]?key|password|private[_-]?key|cookie|secret|token)", re.IGNORECASE)


class ExecutionKind(str, Enum):
    """How a logical role is executed."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"
    HUMAN = "human"
    ORCHESTRATOR = "orchestrator"


class RetryPolicy(StrictModel):
    """Bounded retry policy for one role."""

    max_attempts: int = Field(strict=True, ge=1, le=5)
    backoff_seconds: int = Field(strict=True, ge=0, le=3_600)


class RoleManifest(StrictModel):
    """Complete logical-role contract.

    All operational knobs are explicit so a role cannot inherit a permissive
    tool, workspace, or secret policy by accident.
    """

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    role_id: NonBlankString
    display_name: NonBlankString
    execution_kind: ExecutionKind
    mission: NonBlankString
    non_goals: tuple[NonBlankString, ...] = Field(min_length=1)
    workspace: NonBlankString
    prompt_includes: tuple[NonBlankString, ...]
    primary_model: NonBlankString
    fallback_model: NonBlankString
    reasoning_budget_seconds: int = Field(strict=True, ge=0, le=86_400)
    tools_allow: tuple[NonBlankString, ...]
    tools_deny: tuple[NonBlankString, ...]
    skills_allow: tuple[NonBlankString, ...]
    sandbox_policy: NonBlankString
    spawn_allowlist: tuple[NonBlankString, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    retry_policy: RetryPolicy
    timeout_seconds: int = Field(strict=True, ge=1, le=86_400)
    stop_conditions: tuple[NonBlankString, ...] = Field(min_length=1)
    evidence_policy: NonBlankString
    escalation_policy: NonBlankString
    observability_tags: tuple[NonBlankString, ...] = Field(min_length=1)
    secret_refs: tuple[NonBlankString, ...]
    stages: tuple[NonBlankString, ...] = Field(min_length=1)

    @field_validator("role_id")
    @classmethod
    def validate_role_id(cls, value: str) -> str:
        if not _ROLE_ID.fullmatch(value):
            raise ValueError("role_id must be a lowercase kebab-case identifier")
        return value

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if len(value.split()) < 2:
            raise ValueError("display_name must contain at least two name parts")
        return value

    @field_validator("secret_refs")
    @classmethod
    def validate_secret_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not _SECRET_NAME.fullmatch(value):
                raise ValueError("secret_refs must contain environment/provider names only")
        return values

    @field_validator("input_schema", "output_schema")
    @classmethod
    def validate_json_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("role input_schema/output_schema must describe an object")
        return value

    @field_validator("output_schema")
    @classmethod
    def validate_versioned_output_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        required = value.get("required")
        properties = value.get("properties")
        schema_version = properties.get("schema_version") if isinstance(properties, Mapping) else None
        if (
            not isinstance(required, (list, tuple))
            or "schema_version" not in required
            or not isinstance(schema_version, Mapping)
            or schema_version.get("type") != "string"
        ):
            raise ValueError("role output_schema must require a string schema_version")
        return value

    @model_validator(mode="after")
    def validate_isolation_contract(self) -> RoleManifest:
        if set(self.tools_allow) & set(self.tools_deny):
            raise ValueError(f"role {self.role_id} has tools in both allow and deny lists")
        if self.role_id == "lead-ranker" and any(
            forbidden in " ".join(self.tools_allow).casefold()
            for forbidden in ("crawl", "search", "browser")
        ):
            raise ValueError("lead-ranker must not receive crawl/search/browser tools")

        input_text = _json_text(self.input_schema)
        if self.role_id == "ux-conversion-auditor" and "businessstrength" in input_text:
            raise ValueError("UX role must not receive BusinessStrength")
        if self.role_id == "business-strength" and any(
            forbidden.replace("_", "") in input_text
            for forbidden in ("screenshot", "visual_payload", "design_payload")
        ):
            raise ValueError("business-strength must not receive screenshot/design payload")
        if self.role_id == "technical-auditor" and any(
            forbidden.replace("_", "") in input_text
            for forbidden in ("money_thesis", "commercial_opportunity")
        ):
            raise ValueError("technical-auditor must not receive money thesis")
        return self


class AgentTopology(StrictModel):
    """Validated logical topology and stable stage order."""

    model_config = ConfigDict(extra="forbid", strict=False, frozen=True)

    schema_version: NonBlankString
    stage_flow: tuple[NonBlankString, ...] = Field(min_length=1)
    redesign_stage_flow: tuple[NonBlankString, ...] = Field(min_length=1)
    roles: tuple[RoleManifest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_topology(self) -> AgentTopology:
        if len(set(self.stage_flow)) != len(self.stage_flow):
            raise ValueError("stage_flow contains duplicate stage IDs")
        if len(set(self.redesign_stage_flow)) != len(self.redesign_stage_flow):
            raise ValueError("redesign_stage_flow contains duplicate stage IDs")
        role_ids = [role.role_id for role in self.roles]
        if len(set(role_ids)) != len(role_ids):
            raise ValueError("duplicate role_id in topology")
        display_names = [role.display_name for role in self.roles]
        if len(set(display_names)) != len(display_names):
            raise ValueError("duplicate display_name in topology")
        known = set(self.stage_flow) | set(self.redesign_stage_flow)
        unknown = sorted({stage for role in self.roles for stage in role.stages} - known)
        if unknown:
            raise ValueError(f"role references unknown stage(s): {', '.join(unknown)}")
        return self

    def role(self, role_id: str) -> RoleManifest:
        """Return one role or raise a stable lookup error."""

        for role in self.roles:
            if role.role_id == role_id:
                return role
        raise KeyError(role_id)


# Public alias used by integrations that call the document a manifest.
TopologyManifest = AgentTopology


def _json_text(value: Mapping[str, Any]) -> str:
    # repr is sufficient for the isolation guard and avoids adding a serializer
    # dependency to topology loading.
    return repr(value).replace("_", "").casefold()


def _scan_for_secrets(value: Any, *, key: str = "") -> None:
    """Fail closed if a raw topology contains a credential or its env value."""

    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            child_name = str(child_key)
            if (
                _SECRET_KEY.search(child_name)
                and child_name != "secret_refs"
                and child_value not in (None, "", [], (), {})
            ):
                raise ValueError(f"secret value is not allowed in topology field {child_name}")
            _scan_for_secrets(child_value, key=child_name)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _scan_for_secrets(item, key=key)
        return
    if isinstance(value, str) and len(value) >= 8:
        for name in ("OPENCLAW_WEB_DASHBOARD_MINH_TOKEN", "OPENCLAW_WEB_DASHBOARD_WIEN_TOKEN"):
            secret = os.environ.get(name)
            if secret and value == secret:
                raise ValueError("secret value is not allowed in topology")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError(f"cannot load topology YAML: {path}") from error
    if not isinstance(raw, dict):
        raise TypeError("topology YAML must contain an object")
    _scan_for_secrets(raw)
    return raw


def _tuplify(value: Any) -> Any:
    """Normalize YAML sequences for strict Pydantic tuple fields."""

    if isinstance(value, dict):
        return {key: _tuplify(child) for key, child in value.items()}
    if isinstance(value, list):
        return tuple(_tuplify(child) for child in value)
    return value


def load_topology(path: Path) -> AgentTopology:
    """Load one topology YAML and its role files with fail-closed validation."""

    topology_path = Path(path)
    raw = _load_yaml(topology_path)
    role_values = raw.get("roles")
    if role_values is None:
        role_values = [item.stem for item in sorted((topology_path.parent / "roles").glob("*.yaml"))]
    if not isinstance(role_values, list):
        raise TypeError("topology roles must be a list")

    roles: list[dict[str, Any]] = []
    for item in role_values:
        if isinstance(item, str):
            role_path = topology_path.parent / "roles" / f"{item}.yaml"
            roles.append(_load_yaml(role_path))
        elif isinstance(item, dict):
            _scan_for_secrets(item)
            roles.append(item)
        else:
            raise TypeError("topology roles must contain role IDs or objects")

    raw_role_ids = [item.get("role_id") for item in roles]
    if len(raw_role_ids) != len(set(raw_role_ids)):
        raise ValueError("duplicate role_id in topology")

    payload = dict(raw)
    payload["roles"] = roles
    payload.setdefault("redesign_stage_flow", list(REDESIGN_STAGE_FLOW))
    payload = _tuplify(payload)
    topology = AgentTopology.model_validate(payload)
    validate_topology(topology)
    return topology


def validate_topology(topology: AgentTopology) -> AgentTopology:
    """Validate high-value canonical invariants and return the same object."""

    if not isinstance(topology, AgentTopology):
        raise TypeError("topology must be an AgentTopology")
    expected_stages = set(CANONICAL_STAGE_FLOW)
    expected_redesign = set(REDESIGN_STAGE_FLOW)
    if set(topology.stage_flow) != expected_stages:
        raise ValueError("topology stage_flow must contain the 17 canonical stages")
    if tuple(topology.stage_flow) != CANONICAL_STAGE_FLOW:
        raise ValueError("topology stage_flow must match canonical order")
    if set(topology.redesign_stage_flow) != expected_redesign:
        raise ValueError("topology redesign_stage_flow must contain the canonical sub-stages")
    if tuple(topology.redesign_stage_flow) != REDESIGN_STAGE_FLOW:
        raise ValueError("topology redesign_stage_flow must match canonical order")
    required_roles = {
        "orchestrator",
        "market-intelligence",
        "discovery-scout",
        "entity-resolver",
        "business-strength",
        "agency-fit",
        "fast-web-screener",
        "technical-auditor",
        "ux-conversion-auditor",
        "commercial-opportunity",
        "dealability",
        "evidence-verifier",
        "red-team",
        "lead-ranker",
        "portfolio-selector",
        "dossier-writer",
        "redesign-intelligence",
    }
    actual_roles = {role.role_id for role in topology.roles}
    if actual_roles != required_roles:
        missing = sorted(required_roles - actual_roles)
        extra = sorted(actual_roles - required_roles)
        detail = [f"missing={missing}" if missing else "", f"unknown={extra}" if extra else ""]
        raise ValueError("topology roles do not match canonical roles: " + ", ".join(item for item in detail if item))
    return topology


__all__ = [
    "CANONICAL_STAGE_FLOW",
    "REDESIGN_STAGE_FLOW",
    "AgentTopology",
    "ExecutionKind",
    "RetryPolicy",
    "RoleManifest",
    "TopologyManifest",
    "load_topology",
    "validate_topology",
]
