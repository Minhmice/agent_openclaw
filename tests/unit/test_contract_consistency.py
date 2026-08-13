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
