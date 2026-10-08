from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from openclaw_web.dashboard.auth import ACTOR_IDS
from openclaw_web.dashboard.coordinator import WorkflowDashboardCoordinator
from openclaw_web.dashboard.models import ActionRequest
from openclaw_web.dashboard.read_repository import DashboardReadRepository
from openclaw_web.db.connection import connect
from openclaw_web.db.migrations import migrate
from openclaw_web.db.repository import Repository
from openclaw_web.lead_contracts import PortfolioEntry, RedTeamVerdict


@dataclass
class FakeCommand:
    project_id: str
    action: str
    actor_id: str
    page_slug: str | None


class FakeWorkflowAdapter:
    def __init__(self, workflow_root: Path) -> None:
        self.workflow_root = workflow_root
        self.action_calls: list[FakeCommand] = []
        self.fail_if_called = False

    def action(
        self,
        *,
        project_id: str,
        action: str,
        actor_id: str,
        page_slug: str | None = None,
        reason: str | None = None,
    ) -> FakeCommand:
        del reason
        command = FakeCommand(project_id, action, actor_id, page_slug)
        self.action_calls.append(command)
        return command

    def execute(self, command: FakeCommand) -> SimpleNamespace:
        if self.fail_if_called:
            raise AssertionError("coordinator should have been reconciled without re-running")
        path = self.workflow_root / "projects" / command.project_id / "project.json"
        project = json.loads(path.read_text(encoding="utf-8"))
        project["state_version"] += 1
        if command.action == "select-lead":
            project["lead_state"] = "selected"
        elif command.action == "mark-done":
            page = next(item for item in project["pages"] if item["slug"] == command.page_slug)
            page["status"] = "stakeholder-review"
            page["owner_done_by"] = command.actor_id
        else:
            raise AssertionError(command.action)
        path.write_text(json.dumps(project), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=json.dumps(project))


def _seed(tmp_path: Path) -> tuple[Repository, DashboardReadRepository, Path, FakeWorkflowAdapter]:
    database = tmp_path / "state.sqlite"
    workflow_root = tmp_path / "workflow"
    project_file = workflow_root / "projects" / "project-dashboard" / "project.json"
    project_file.parent.mkdir(parents=True)
    project_file.write_text(
        json.dumps(
            {
                "project_id": "project-dashboard",
                "status": "review",
                "state_version": 0,
                "pages": [
                    {
                        "slug": "homepage",
                        "status": "in-progress",
                        "owner_id": ACTOR_IDS["minh"],
                        "checklist_complete": False,
                        "next_action": "page-done",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    connection = connect(database)
    migrate(connection)
    connection.execute(
        "INSERT INTO candidates (candidate_id, canonical_domain, normalized_name, state, snapshot_json) "
        "VALUES ('candidate-dashboard', 'example.com', 'dashboard', 'review-ready', '{}')"
    )
    connection.execute(
        "INSERT INTO projects (project_id, candidate_id, state, state_version, snapshot_json) "
        "VALUES ('project-dashboard', 'candidate-dashboard', 'review', 0, '{}')"
    )
    Repository(connection).create_portfolio(
        portfolio_id="portfolio-dashboard",
        run_id=None,
        status="complete",
        entries=(
            PortfolioEntry(
                entry_id="entry-dashboard",
                portfolio_id="portfolio-dashboard",
                candidate_id="candidate-dashboard",
                rank=1,
                company_name="Dashboard Co",
                website_url="https://example.com",
                business_strength=80,
                agency_fit=80,
                digital_gap=80,
                evidence_confidence=0.9,
                red_team_verdict=RedTeamVerdict.SURVIVE,
            ),
        ),
        target=3,
        maximum=7,
    )
    connection.commit()
    repository = Repository(connection)
    read_repository = DashboardReadRepository(database)
    adapter = FakeWorkflowAdapter(workflow_root)
    return repository, read_repository, workflow_root, adapter


def _request(action: str, *, page_slug: str | None = None) -> ActionRequest:
    return ActionRequest(
        action=action,
        target_id="entry-dashboard",
        page_slug=page_slug,
        expected_state_version=0,
        idempotency_key=f"dashboard-{action}-{page_slug or 'lead'}",
    )


def test_workflow_dashboard_coordinator_syncs_project_lead_state_and_pages(tmp_path: Path) -> None:
    repository, read_repository, workflow_root, adapter = _seed(tmp_path)
    coordinator = WorkflowDashboardCoordinator(
        command_adapter=adapter,
        repository=repository,
        read_repository=read_repository,
        workflow_root=workflow_root,
    )

    result = coordinator.apply(_request("select-lead"), ACTOR_IDS["minh"])

    assert result == {
        "status": "accepted",
        "state": "selected",
        "state_version": 1,
        "next_action": "human-approval",
        "message_vi": "Đã ghi nhận lead ở trạng thái selected.",
    }
    row = repository.connection.execute(
        "SELECT state, state_version, snapshot_json FROM projects WHERE project_id = 'project-dashboard'"
    ).fetchone()
    assert (row["state"], row["state_version"]) == ("review", 1)
    assert json.loads(row["snapshot_json"])["lead_state"] == "selected"


def test_workflow_dashboard_coordinator_reconciles_crash_window_without_replaying_command(
    tmp_path: Path,
) -> None:
    repository, read_repository, workflow_root, adapter = _seed(tmp_path)
    project_file = workflow_root / "projects" / "project-dashboard" / "project.json"
    project = json.loads(project_file.read_text(encoding="utf-8"))
    project.update({"lead_state": "selected", "state_version": 1})
    project_file.write_text(json.dumps(project), encoding="utf-8")
    adapter.fail_if_called = True
    coordinator = WorkflowDashboardCoordinator(
        command_adapter=adapter,
        repository=repository,
        read_repository=read_repository,
        workflow_root=workflow_root,
    )

    result = coordinator.apply(_request("select-lead"), ACTOR_IDS["minh"])

    assert result["state"] == "selected"
    assert result["state_version"] == 1
    assert adapter.action_calls == []
    assert repository.get_project_state_version("project-dashboard") == 1


def test_workflow_dashboard_coordinator_syncs_page_action_snapshot(tmp_path: Path) -> None:
    repository, read_repository, workflow_root, adapter = _seed(tmp_path)
    coordinator = WorkflowDashboardCoordinator(
        command_adapter=adapter,
        repository=repository,
        read_repository=read_repository,
        workflow_root=workflow_root,
    )

    result = coordinator.apply(_request("page-done", page_slug="homepage"), ACTOR_IDS["minh"])

    assert result["state"] == "completed"
    lead = read_repository.get_lead("entry-dashboard")
    assert lead is not None
    assert lead.pages[0].status == "stakeholder-review"
    assert lead.state_version == 1
