"""Workflow-coordinator adapter used by the dashboard write boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from openclaw_web.db.repository import Repository
from openclaw_web.models import ProjectState

from .models import ActionRequest, DashboardAction
from .read_repository import DashboardReadRepository

_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_SAFE_PAGE_VALUE = re.compile(r"^[A-Za-z0-9_.:@/ +()\-]{1,200}$")
_PAGE_KEYS = frozenset(
    {
        "slug",
        "status",
        "owner_id",
        "assignee_id",
        "owner",
        "assignee",
        "checklist_complete",
        "next_action",
    }
)
_COMMANDS: Mapping[DashboardAction, str] = {
    DashboardAction.SELECT_LEAD: "select-lead",
    DashboardAction.WATCH_LEAD: "watch-lead",
    DashboardAction.LEAD_APPROVE: "approve",
    DashboardAction.LEAD_REJECT: "reject",
    DashboardAction.LEAD_REQUEST_CHANGE: "request-changes",
    DashboardAction.PAGE_STATUS: "page-status",
    DashboardAction.PAGE_DONE: "mark-done",
    DashboardAction.PAGE_APPROVE: "page-approve",
    DashboardAction.BLOCK: "block",
    DashboardAction.FINAL_CONFIRM: "final-confirm",
}
_LEAD_STATES: Mapping[DashboardAction, str] = {
    DashboardAction.SELECT_LEAD: "selected",
    DashboardAction.WATCH_LEAD: "watching",
    DashboardAction.LEAD_APPROVE: "approved",
    DashboardAction.LEAD_REJECT: "rejected",
    DashboardAction.LEAD_REQUEST_CHANGE: "awaiting-command",
}
_PAGE_ACTIONS = frozenset(
    {
        DashboardAction.PAGE_STATUS,
        DashboardAction.PAGE_DONE,
        DashboardAction.PAGE_APPROVE,
        DashboardAction.BLOCK,
    }
)
_READ_ONLY_ACTIONS = frozenset({DashboardAction.PAGE_STATUS})
_RESPONSE_STATES: Mapping[DashboardAction, str] = {
    DashboardAction.PAGE_DONE: "completed",
    DashboardAction.PAGE_APPROVE: "approved",
    DashboardAction.BLOCK: "blocked",
    DashboardAction.FINAL_CONFIRM: "completed",
}


class WorkflowCommandAdapter(Protocol):
    """Small structural subset of ``WorkflowCoordinatorAdapter``."""

    def action(
        self,
        *,
        project_id: str,
        action: str,
        actor_id: str,
        page_slug: str | None = None,
        reason: str | None = None,
    ) -> object: ...

    def execute(self, command: object) -> object: ...


class WorkflowDashboardCoordinator:
    """Route dashboard actions and reconcile the SQLite read model.

    The workflow JSON remains the source of truth.  SQLite receives only the
    coordinator's state version plus a small allowlisted projection, which
    keeps the HTTP service from becoming a second workflow state machine.
    """

    def __init__(
        self,
        *,
        command_adapter: WorkflowCommandAdapter,
        repository: Repository,
        read_repository: DashboardReadRepository,
        workflow_root: Path,
    ) -> None:
        self.command_adapter = command_adapter
        self.repository = repository
        self.read_repository = read_repository
        self.workflow_root = Path(workflow_root).resolve()

    @staticmethod
    def _project_id(value: str) -> str:
        if not isinstance(value, str) or _PROJECT_ID.fullmatch(value) is None:
            raise RuntimeError("workflow project identity is invalid")
        return value

    def _resolve_project_id(self, request: ActionRequest) -> str:
        lead = self.read_repository.get_lead(request.target_id)
        if lead is not None and lead.project_id is not None:
            return self._project_id(lead.project_id)
        return self._project_id(request.target_id)

    def _project_file(self, project_id: str) -> Path:
        projects_root = (self.workflow_root / "projects").resolve(strict=True)
        project_dir = projects_root / project_id
        if project_dir.is_symlink():
            raise RuntimeError("workflow project path is not safe")
        project_path = project_dir / "project.json"
        if project_path.is_symlink() or not project_path.is_file():
            raise RuntimeError("workflow project snapshot is unavailable")
        resolved = project_path.resolve(strict=True)
        try:
            resolved.relative_to(projects_root)
        except ValueError as error:
            raise RuntimeError("workflow project path is not safe") from error
        return resolved

    def _load_project(self, project_id: str) -> dict[str, Any]:
        path = self._project_file(project_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, RecursionError) as error:
            raise RuntimeError("workflow project snapshot is invalid") from error
        if not isinstance(payload, dict) or payload.get("project_id") != project_id:
            raise RuntimeError("workflow project snapshot is invalid")
        return payload

    @staticmethod
    def _version(project: Mapping[str, Any]) -> int:
        value = project.get("state_version")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError("workflow project version is invalid")
        return value

    @staticmethod
    def _state(project: Mapping[str, Any]) -> ProjectState:
        value = project.get("status")
        try:
            return ProjectState(value)
        except (TypeError, ValueError) as error:
            raise RuntimeError("workflow project state is invalid") from error

    @staticmethod
    def _safe_pages(project: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_pages = project.get("pages", [])
        if raw_pages is None:
            return []
        if not isinstance(raw_pages, list) or len(raw_pages) > 50:
            raise RuntimeError("workflow page snapshot is invalid")
        pages: list[dict[str, Any]] = []
        for raw_page in raw_pages:
            if not isinstance(raw_page, dict):
                raise TypeError("workflow page snapshot is invalid")
            slug = raw_page.get("slug")
            status = raw_page.get("status")
            if (
                not isinstance(slug, str)
                or _SAFE_PAGE_VALUE.fullmatch(slug) is None
                or not isinstance(status, str)
                or _SAFE_PAGE_VALUE.fullmatch(status) is None
            ):
                raise RuntimeError("workflow page snapshot is invalid")
            page: dict[str, Any] = {"slug": slug, "status": status}
            for key in _PAGE_KEYS - {"slug", "status"}:
                value = raw_page.get(key)
                if value is None:
                    continue
                if key == "checklist_complete":
                    if not isinstance(value, bool):
                        raise RuntimeError("workflow page snapshot is invalid")
                elif not isinstance(value, str) or _SAFE_PAGE_VALUE.fullmatch(value) is None:
                    # Optional display metadata is omitted rather than allowing
                    # arbitrary workflow text into the dashboard read model.
                    continue
                page[key] = value
            pages.append(page)
        return pages

    @staticmethod
    def _page(project: Mapping[str, Any], slug: str | None) -> Mapping[str, Any] | None:
        if slug is None or not isinstance(project.get("pages"), list):
            return None
        return next(
            (
                item
                for item in project["pages"]
                if isinstance(item, dict) and item.get("slug") == slug
            ),
            None,
        )

    @classmethod
    def _already_applied(
        cls,
        request: ActionRequest,
        actor_id: str,
        project: Mapping[str, Any],
    ) -> bool:
        if request.action in _READ_ONLY_ACTIONS:
            return False
        if cls._version(project) != request.expected_state_version + 1:
            return False
        if request.action in _LEAD_STATES:
            if request.action is DashboardAction.LEAD_APPROVE:
                return project.get("status") == "approved" and project.get("approved_by") == actor_id
            if request.action is DashboardAction.LEAD_REJECT:
                return project.get("status") == "rejected" and project.get("rejected_by") == actor_id
            if request.action is DashboardAction.LEAD_REQUEST_CHANGE:
                changes = project.get("change_requests")
                last = changes[-1] if isinstance(changes, list) and changes else None
                return (
                    project.get("status") == "review"
                    and isinstance(last, dict)
                    and last.get("actor") == actor_id
                )
            return project.get("lead_state") == _LEAD_STATES[request.action]
        page = cls._page(project, request.page_slug)
        if page is None:
            return False
        if request.action is DashboardAction.PAGE_DONE:
            return page.get("status") == "stakeholder-review" and page.get("owner_done_by") == actor_id
        if request.action is DashboardAction.PAGE_APPROVE:
            return page.get("status") == "approved" and page.get("approved_by") == actor_id
        if request.action is DashboardAction.BLOCK:
            return page.get("status") == "blocked" and page.get("blocked_by") == actor_id
        if request.action is DashboardAction.FINAL_CONFIRM:
            confirmations = project.get("final_confirmations")
            return isinstance(confirmations, dict) and actor_id in confirmations
        return False

    @staticmethod
    def _response_state(request: ActionRequest, project: Mapping[str, Any]) -> str:
        if request.action in _LEAD_STATES:
            return _LEAD_STATES[request.action]
        if request.action in _RESPONSE_STATES:
            return _RESPONSE_STATES[request.action]
        page = WorkflowDashboardCoordinator._page(project, request.page_slug)
        status = page.get("status") if page is not None else None
        return status if isinstance(status, str) and _SAFE_PAGE_VALUE.fullmatch(status) else "in-progress"

    @staticmethod
    def _next_action(request: ActionRequest, project: Mapping[str, Any]) -> str | None:
        if request.action in {DashboardAction.LEAD_REJECT}:
            return None
        if request.action in {DashboardAction.SELECT_LEAD, DashboardAction.WATCH_LEAD}:
            return "human-approval"
        if request.action is DashboardAction.LEAD_APPROVE:
            return "website-brief"
        if request.action is DashboardAction.LEAD_REQUEST_CHANGE:
            return "human-approval"
        if request.action in _PAGE_ACTIONS:
            page = WorkflowDashboardCoordinator._page(project, request.page_slug)
            value = page.get("next_action") if page is not None else None
            return value if isinstance(value, str) and _SAFE_PAGE_VALUE.fullmatch(value) else None
        if request.action is DashboardAction.FINAL_CONFIRM:
            return "offer-ready" if project.get("status") == "offer-ready" else "final-confirm"
        return None

    @staticmethod
    def _message(request: ActionRequest, *, reconciled: bool) -> str:
        if reconciled:
            return "Đã khôi phục action dashboard sau lần chạy trước."
        if request.action in _LEAD_STATES:
            return f"Đã ghi nhận lead ở trạng thái {_LEAD_STATES[request.action]}."
        return "Đã ghi nhận action dashboard qua workflow coordinator."

    def _synchronize(
        self,
        request: ActionRequest,
        actor_id: str,
        project_id: str,
        project: Mapping[str, Any],
        *,
        reconciled: bool,
    ) -> Mapping[str, object]:
        state = self._state(project)
        state_version = self._version(project)
        if request.action in _READ_ONLY_ACTIONS:
            if state_version != request.expected_state_version:
                raise RuntimeError("workflow project changed during dashboard action")
        elif state_version != request.expected_state_version + 1:
            raise RuntimeError("workflow coordinator returned an invalid state version")

        snapshot_updates: dict[str, Any] = {"pages": self._safe_pages(project)}
        lead_state = _LEAD_STATES.get(request.action)
        if lead_state is not None:
            snapshot_updates["lead_state"] = lead_state
        if not self.repository.synchronize_project_state(
            project_id,
            expected_version=request.expected_state_version,
            state=state,
            state_version=state_version,
            snapshot_updates=snapshot_updates,
        ):
            raise RuntimeError("workflow project changed during dashboard synchronization")
        return {
            "status": "accepted",
            "state": self._response_state(request, project),
            "state_version": state_version,
            "next_action": self._next_action(request, project),
            "message_vi": self._message(request, reconciled=reconciled),
        }

    def apply(self, request: ActionRequest, actor_id: str) -> Mapping[str, object]:
        """Apply one authenticated action, reconciling a crash window if needed."""

        if not isinstance(request, ActionRequest) or not isinstance(actor_id, str) or not actor_id.strip():
            raise TypeError("request and actor_id are required")
        project_id = self._resolve_project_id(request)
        before = self._load_project(project_id)
        if self._already_applied(request, actor_id, before):
            return self._synchronize(
                request,
                actor_id,
                project_id,
                before,
                reconciled=True,
            )

        command = self.command_adapter.action(
            project_id=project_id,
            action=_COMMANDS[request.action],
            actor_id=actor_id,
            page_slug=request.page_slug,
            reason=request.reason,
        )
        completed = self.command_adapter.execute(command)
        if getattr(completed, "returncode", 1) != 0:
            raise RuntimeError("workflow coordinator rejected dashboard action")
        after = self._load_project(project_id)
        return self._synchronize(
            request,
            actor_id,
            project_id,
            after,
            reconciled=False,
        )


__all__ = ["WorkflowCommandAdapter", "WorkflowDashboardCoordinator"]
