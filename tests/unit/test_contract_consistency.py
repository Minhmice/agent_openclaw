from __future__ import annotations

from pathlib import Path

MINH_ID = "620891893659598850"
WIEN_ID = "859783610625556480"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_curie_and_pm_contracts_allow_both_review_approvers() -> None:
    root = _repo_root()
    curie = (root / "agents/shared/contracts/curie-to-website.yaml").read_text(encoding="utf-8")
    website_to_pm = (root / "agents/shared/contracts/website-to-pm.yaml").read_text(encoding="utf-8")

    assert MINH_ID in curie
    assert WIEN_ID in curie
    assert "without Minh's actor ID" not in curie
    assert MINH_ID in website_to_pm
    assert WIEN_ID in website_to_pm


def test_docs_use_component_actions_and_current_typed_commands() -> None:
    root = _repo_root()
    paths = (
        root / "README.md",
        root / "agents/shared/COORDINATOR.md",
        root / "agents/shared/contracts/workflow-commands.md",
        root / "agents/project-pm/README.md",
        root / "agents/website-brief/README.md",
        root / "docs/runbooks/web-audit-discovery.md",
    )
    docs = "\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())

    assert "/finalize <project>" not in docs
    assert "/final-confirm <project_id>" in docs
    assert "/page-approve <project_id> <page_slug>" in docs
    assert "Components v2" in docs
    assert f"{MINH_ID}" in docs and f"{WIEN_ID}" in docs


def test_operational_runbook_covers_local_market_cron_and_rollback() -> None:
    runbook = (_repo_root() / "docs/runbooks/web-audit-discovery.md").read_text(encoding="utf-8")

    for required in (
        "config/markets/hanoi-80km.yaml",
        "07:30 Asia/Bangkok",
        "typed fallback",
        "retention",
        "backup",
        "rollback",
        "openclaw-web health",
        "openclaw-web cron-run --dry-run",
    ):
        assert required in runbook


def test_discuss_discovery_contract_is_bounded_and_uses_canonical_runtime_paths() -> None:
    root = _repo_root()
    coordinator = (root / "agents/shared/COORDINATOR.md").read_text(encoding="utf-8")
    intent = (root / "agents/shared/contracts/discuss-intents.md").read_text(encoding="utf-8")
    handoff = (root / "agents/shared/contracts/curie-handoff.md").read_text(encoding="utf-8")

    docs = f"{coordinator}\n{intent}\n{handoff}"
    assert "target=\"channel:<id>\"" in docs
    assert "/home/minhmice/.openclaw/workflow/projects/<project_id>" in docs
    assert "/home/minhmice/.openclaw/workspace/workflow/projects" in docs
    assert "shared instructions" in docs
    assert "sessions_spawn" in docs and "allowAgents" in docs
    assert ("Do not fall back to" in docs or "must never fall back" in docs) and "web_search" in docs
    assert "return `no_candidate_defensible`" in docs
    assert "bounded" in docs.lower()


def test_website_redesign_prompt_stack_is_role_scoped() -> None:
    root = _repo_root()
    shared = root / "agents/shared/contracts"
    required = (
        shared / "website-redesign-policy.md",
        shared / "website-redesign-research.md",
        shared / "website-redesign-design.md",
        shared / "website-redesign-pm.md",
        shared / "website-redesign-output-schema.md",
    )

    assert all(path.is_file() for path in required)
    assert len((root / "agents/shared/website-redesign-agent-spec.md").read_text(encoding="utf-8").splitlines()) < 200
    assert "compatibility" in (root / "agents/shared/website-redesign-agent-spec.md").read_text(
        encoding="utf-8"
    ).lower()

    role_routes = {
        root / "agents/curie/README.md": "website-redesign-research.md",
        root / "agents/website-brief/README.md": "website-redesign-design.md",
        root / "agents/project-pm/README.md": "website-redesign-pm.md",
    }
    for readme, contract in role_routes.items():
        content = readme.read_text(encoding="utf-8")
        assert contract in content
        assert "website-redesign-agent-spec.md" not in content
