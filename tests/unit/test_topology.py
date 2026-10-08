import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openclaw_web.topology import CANONICAL_STAGE_FLOW, RoleManifest, load_topology

FRIENDLY_AGENT_NAMES = {
    "orchestrator": "An Minh",
    "market-intelligence": "Ngọc Hân",
    "discovery-scout": "Gia Linh",
    "entity-resolver": "Khánh An",
    "business-strength": "Đức Minh",
    "agency-fit": "Thanh Vy",
    "fast-web-screener": "Yến Nhi",
    "technical-auditor": "Hoàng Nam",
    "ux-conversion-auditor": "Mai Anh",
    "commercial-opportunity": "Tuệ Lâm",
    "dealability": "Bảo Ngọc",
    "evidence-verifier": "Nhật Minh",
    "red-team": "Hải Yến",
    "lead-ranker": "Minh Châu",
    "portfolio-selector": "Quỳnh Anh",
    "dossier-writer": "Thảo My",
    "redesign-intelligence": "Yến My",
}


def test_repository_topology_loads_all_canonical_roles() -> None:
    topology = load_topology(Path("config/agents/topology.yaml"))

    assert topology.stage_flow[0] == "define_market"
    assert topology.stage_flow[-1] == "redesign_intelligence"
    assert len(topology.roles) == 17
    assert {role.role_id for role in topology.roles} == {
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


def test_repository_topology_exposes_unique_friendly_vietnamese_names() -> None:
    topology = load_topology(Path("config/agents/topology.yaml"))

    assert {role.role_id: role.display_name for role in topology.roles} == FRIENDLY_AGENT_NAMES
    assert len({role.display_name for role in topology.roles}) == len(topology.roles)
    assert all(" " in role.display_name for role in topology.roles)


def test_topology_rejects_duplicate_display_names(tmp_path: Path) -> None:
    payload = yaml.safe_load(Path("config/agents/topology.yaml").read_text(encoding="utf-8"))
    role = {
        "role_id": "first-role",
        "display_name": "Mai Anh",
        "execution_kind": "deterministic",
        "mission": "test",
        "non_goals": ["test"],
        "workspace": "local",
        "prompt_includes": [],
        "primary_model": "none",
        "fallback_model": "none",
        "reasoning_budget_seconds": 1,
        "tools_allow": [],
        "tools_deny": ["exec"],
        "skills_allow": [],
        "sandbox_policy": "restricted",
        "spawn_allowlist": [],
        "input_schema": {"type": "object"},
        "output_schema": {
            "type": "object",
            "required": ["schema_version"],
            "properties": {"schema_version": {"type": "string"}},
        },
        "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
        "timeout_seconds": 1,
        "stop_conditions": ["done"],
        "evidence_policy": "none",
        "escalation_policy": "none",
        "observability_tags": ["test"],
        "secret_refs": [],
        "stages": ["define_market"],
    }
    second_role = role.copy()
    second_role["role_id"] = "second-role"
    payload["roles"] = [
        role,
        second_role,
    ]
    path = tmp_path / "topology.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate display_name"):
        load_topology(path)


def test_every_role_output_schema_requires_a_schema_version() -> None:
    topology = load_topology(Path("config/agents/topology.yaml"))

    for role in topology.roles:
        required = role.output_schema.get("required", ())
        properties = role.output_schema.get("properties", {})
        assert "schema_version" in required
        assert isinstance(properties, dict)
        assert "schema_version" in properties


def test_topology_rejects_missing_required_role_field(tmp_path: Path) -> None:
    path = tmp_path / "topology.yaml"
    path.write_text(
        """
schema_version: lead-intelligence.topology.v1
stage_flow: [define_market]
roles:
  - role_id: broken
    execution_kind: deterministic
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_topology(path)


def test_topology_rejects_duplicate_role_ids(tmp_path: Path) -> None:
    path = tmp_path / "topology.yaml"
    role = {
        "role_id": "same",
        "execution_kind": "deterministic",
        "mission": "test",
        "non_goals": ["test"],
        "workspace": "local",
        "prompt_includes": [],
        "primary_model": "none",
        "fallback_model": "none",
        "reasoning_budget_seconds": 1,
        "tools_allow": [],
        "tools_deny": ["exec"],
        "skills_allow": [],
        "sandbox_policy": "restricted",
        "spawn_allowlist": [],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
        "timeout_seconds": 1,
        "stop_conditions": ["done"],
        "evidence_policy": "none",
        "escalation_policy": "none",
        "observability_tags": ["test"],
        "secret_refs": [],
    }
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "lead-intelligence.topology.v1",
                "stage_flow": ["define_market"],
                "roles": [role, role.copy()],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate role_id"):
        load_topology(path)


def test_topology_rejects_canonical_stage_order_change(tmp_path: Path) -> None:
    payload = yaml.safe_load(Path("config/agents/topology.yaml").read_text(encoding="utf-8"))
    swapped = list(CANONICAL_STAGE_FLOW)
    swapped[4], swapped[5] = swapped[5], swapped[4]
    payload["stage_flow"] = swapped
    path = tmp_path / "topology.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    shutil.copytree(Path("config/agents/roles"), tmp_path / "roles")

    with pytest.raises(ValueError, match="stage_flow must match canonical order"):
        load_topology(path)


def test_topology_rejects_literal_secret_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_WEB_DASHBOARD_MINH_TOKEN", "top-secret-value")
    path = tmp_path / "topology.yaml"
    path.write_text(
        """
schema_version: lead-intelligence.topology.v1
stage_flow: [define_market]
roles: []
literal: top-secret-value
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="secret"):
        load_topology(path)


@pytest.mark.parametrize(
    ("role_file", "forbidden_field"),
    (
        ("business-strength.yaml", "visual_payload"),
        ("business-strength.yaml", "design_payload"),
        ("technical-auditor.yaml", "money_thesis"),
        ("technical-auditor.yaml", "commercial_opportunity"),
    ),
)
def test_role_isolation_rejects_normalized_forbidden_payload(
    role_file: str,
    forbidden_field: str,
) -> None:
    payload = yaml.safe_load(
        (Path("config/agents/roles") / role_file).read_text(encoding="utf-8")
    )
    payload["input_schema"] = {
        "type": "object",
        "properties": {forbidden_field: {"type": "object"}},
    }

    with pytest.raises(ValueError, match="must not receive"):
        RoleManifest.model_validate(payload)
