"""Discord component action and callback capability boundary."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openclaw_web.delivery.components import (
    DEFAULT_REVIEW_REJECTION_REASON,
    ActionResult,
    ComponentActionEnvelope,
    ComponentActionService,
    ComponentSetRecord,
    component_record,
    parse_component_action_json,
)
from openclaw_web.delivery.workflow_adapter import WorkflowCoordinatorAdapter
from openclaw_web.models import ProjectState
from openclaw_web.runtime_support import state_db, workflow_root

DEFAULT_DISCORD_GUILD_ID = "1446612692910739637"
_CALLBACK_KEYS = frozenset({"actor_id", "guild_id", "message_id", "value"})
_DISCORD_SNOWFLAKE = re.compile(r"^[0-9]{17,20}$")


class CoordinatorActions:
    def __init__(
        self,
        adapter: WorkflowCoordinatorAdapter,
        repository: Any,
        workflow_root_factory: Callable[[], Path] = workflow_root,
    ) -> None:
        self.adapter = adapter
        self.repository = repository
        self.workflow_root_factory = workflow_root_factory

    def execute(self, **kwargs: object) -> object:
        command = self.adapter.action(
            project_id=str(kwargs["project_id"]),
            action=str(kwargs["action"]),
            actor_id=str(kwargs["actor_id"]),
            page_slug=str(kwargs["page_slug"]) if kwargs.get("page_slug") else None,
            reason=str(kwargs["reason"]) if kwargs.get("reason") else None,
        )
        completed = self.adapter.execute(command)
        if completed.returncode != 0:
            raise RuntimeError("workflow coordinator rejected component action")
        try:
            payload = json.loads(completed.stdout)
            if not isinstance(payload, dict) or "state_version" not in payload:
                raise ValueError("coordinator output is not a project snapshot")
            state = payload.get("status")
            state_version = payload.get("state_version")
            project_state = ProjectState(state)
        except (TypeError, ValueError, KeyError):
            # Page/status commands can return only a page object; the canonical project
            # file remains the source for the version in that case.
            project_file = (
                self.workflow_root_factory()
                / "projects"
                / str(kwargs["project_id"])
                / "project.json"
            )
            try:
                payload = json.loads(project_file.read_text(encoding="utf-8"))
                project_state = ProjectState(payload["status"])
                state_version = payload["state_version"]
            except (OSError, TypeError, ValueError, KeyError) as error:
                raise RuntimeError("workflow coordinator returned invalid project state") from error
        expected_version = kwargs.get("state_version")
        if expected_version is None:
            expected_version = state_version - 1 if isinstance(state_version, int) else -1
        if (
            isinstance(state_version, bool)
            or not isinstance(state_version, int)
            or isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
        ):
            raise TypeError("workflow coordinator returned invalid project state")
        if str(kwargs["action"]) == "page-status":
            if state_version != expected_version:
                raise TypeError("workflow coordinator returned invalid project state")
            return completed
        if state_version <= expected_version:
            raise TypeError("workflow coordinator returned invalid project state")
        self.repository.confirm_component_action(
            str(kwargs["component_set_id"]),
            str(kwargs["actor_id"]),
            str(kwargs["action"]),
            state=project_state,
            state_version=state_version,
        )
        if not self.repository.synchronize_project_state(
            str(kwargs["project_id"]),
            expected_version=expected_version,
            state=project_state,
            state_version=state_version,
        ):
            raise RuntimeError("workflow project state changed during synchronization")
        return completed


def strict_callback_json(text: str) -> dict[str, str]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("invalid component callback envelope")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("invalid component callback envelope")

    try:
        decoded = json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as error:
        raise ValueError("invalid component callback envelope") from error
    if not isinstance(decoded, dict) or set(decoded) != _CALLBACK_KEYS:
        raise ValueError("invalid component callback envelope")
    result: dict[str, str] = {}
    for key in _CALLBACK_KEYS:
        value = decoded[key]
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("invalid component callback envelope")
        result[key] = value
    if (
        _DISCORD_SNOWFLAKE.fullmatch(result["actor_id"]) is None
        or _DISCORD_SNOWFLAKE.fullmatch(result["guild_id"]) is None
        or _DISCORD_SNOWFLAKE.fullmatch(result["message_id"]) is None
        or len(result["value"].encode("utf-8")) > 512
    ):
        raise ValueError("invalid component callback envelope")
    configured_guild = os.environ.get("OPENCLAW_WEB_DISCORD_GUILD_ID", DEFAULT_DISCORD_GUILD_ID)
    if result["guild_id"] != configured_guild:
        raise ValueError("invalid component callback envelope")
    return result


def canonical_callback_envelope(
    payload: dict[str, str], component: ComponentSetRecord
) -> ComponentActionEnvelope | None:
    parts = payload["value"].split(":")
    page_slug: str | None = None
    if len(parts) == 3 and parts[0] == "project":
        _, project_id, action = parts
    elif len(parts) == 5 and parts[0] == "project" and parts[2] == "page":
        _, project_id, _, page_slug, action = parts
    else:
        return None
    if (
        project_id != component.project_id
        or action not in component.allowed_actions
        or (component.card_type == "page" and page_slug != component.page_slug)
        or (component.card_type != "page" and page_slug is not None)
    ):
        return None
    return ComponentActionEnvelope(
        actor_id=payload["actor_id"],
        channel_id=component.channel_id,
        component_set_id=component.component_set_id,
        message_id=component.message_id,
        project_id=component.project_id,
        state_version=component.state_version,
        action=action,
        page_slug=page_slug,
    )


def workflow_project(component: ComponentSetRecord) -> dict[str, object]:
    path = workflow_root() / "projects" / component.project_id / "project.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise RuntimeError("workflow project artifact is unavailable") from error
    if not isinstance(payload, dict) or payload.get("project_id") != component.project_id:
        raise RuntimeError("workflow project artifact is invalid")
    return payload


def has_unresolved_priority(page: object) -> bool:
    return isinstance(page, dict) and (
        page.get("unresolved_priority") in {"P0", "P1"}
        or page.get("unresolved_p0") is True
        or page.get("unresolved_p1") is True
    )


def component_gate_ready(component: ComponentSetRecord) -> bool:
    if not set(component.allowed_actions) & {"approve", "final-confirm", "page-approve"}:
        return True
    project = workflow_project(component)
    pages = project.get("pages")
    if not isinstance(pages, list):
        return False
    if component.card_type == "page":
        page = next(
            (
                item
                for item in pages
                if isinstance(item, dict) and item.get("slug") == component.page_slug
            ),
            None,
        )
        if not isinstance(page, dict):
            return False
        checklist = page.get("checklist")
        checklist_ready = page.get("checklist_complete") is True or (
            isinstance(checklist, list)
            and bool(checklist)
            and all(
                isinstance(item, dict)
                and str(item.get("status", "")).casefold()
                in {"done", "complete", "completed", "approved", "pass", "passed"}
                for item in checklist
            )
        )
        return checklist_ready and not has_unresolved_priority(page)
    if any(has_unresolved_priority(page) for page in pages):
        return False
    if component.card_type == "final":
        return bool(pages) and all(
            isinstance(page, dict) and page.get("status") == "approved" for page in pages
        )
    return True


def component_read_only(component: ComponentSetRecord, action: str) -> str:
    project = workflow_project(component)
    if action == "refresh":
        return (
            f"Project {component.project_id}: trạng thái {project.get('status', 'unknown')}, "
            f"state_version {project.get('state_version', 'unknown')}."
        )
    if action == "view-unresolved":
        pages = project.get("pages")
        count = (
            sum(1 for page in pages if has_unresolved_priority(page))
            if isinstance(pages, list)
            else 0
        )
        return f"Project {component.project_id}: còn {count} page có P0/P1 chưa xử lý."
    if action == "view-evidence":
        artifact_dir = project.get("artifact_dir")
        available: list[str] = []
        if isinstance(artifact_dir, str) and artifact_dir:
            evidence = Path(artifact_dir) / "evidence"
            available = sorted(path.name for path in evidence.glob("*.json") if path.is_file())
        if not available:
            project_dir = (workflow_root() / "projects" / component.project_id).resolve()
            workflow_root_path = workflow_root().resolve()
            if project_dir.parent != workflow_root_path / "projects":
                raise RuntimeError("workflow evidence artifact is unavailable")
            for key in ("dossier_file", "image_inventory_file"):
                name = project.get(key)
                if not isinstance(name, str) or not name.strip():
                    continue
                candidate = (project_dir / name).resolve()
                if candidate.parent == project_dir and candidate.is_file():
                    available.append(candidate.name)
        if not available:
            raise RuntimeError("workflow evidence artifact is unavailable")
        return f"Project {component.project_id}: evidence gồm {', '.join(available)}."
    raise ValueError("unsupported read-only component action")


def project_snapshot_text(project: dict[str, object] | None) -> tuple[str, str]:
    if project is None:
        return "unknown", "unknown"
    state = project.get("status", project.get("state", "unknown"))
    version = project.get("state_version", "unknown")
    state_text = str(state).strip() or "unknown"
    version_text = str(version).strip() or "unknown"
    return state_text[:80], version_text[:40]


def bounded_project_value(
    project: dict[str, object] | None,
    keys: tuple[str, ...],
    default: str,
    *,
    limit: int = 240,
) -> str:
    if project is not None:
        for key in keys:
            value = project.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:limit]
    return default


def component_result_message(
    envelope: ComponentActionEnvelope,
    result: ActionResult,
    project: dict[str, object] | None,
) -> str:
    """Render one bounded Vietnamese callback result without exposing internals."""
    project_id = envelope.project_id
    state, version = project_snapshot_text(project)
    action_labels = {
        "approve": "duyệt",
        "reject": "từ chối",
        "view-evidence": "xem evidence",
        "view-unresolved": "xem unresolved",
        "refresh": "làm mới",
    }
    action_label = action_labels.get(envelope.action, envelope.action)

    if result.status == "accepted":
        if envelope.action == "approve":
            return f"Đã duyệt {project_id}. Trạng thái mới: {state}. Bước tiếp theo: Website Brief."
        if envelope.action == "reject":
            reason = bounded_project_value(
                project,
                ("rejection_reason", "discard_reason"),
                DEFAULT_REVIEW_REJECTION_REASON,
            )
            return f"Đã từ chối {project_id}. Lý do: {reason} Trạng thái mới: {state}."
        return f"Đã ghi nhận thao tác {action_label} cho {project_id}. Trạng thái mới: {state}."

    if result.status == "read-only":
        if envelope.action == "refresh":
            return f"Refresh {project_id}: trạng thái {state}, state_version {version}."
        detail = result.message_vi
        prefix = f"Project {project_id}: "
        detail = detail.removeprefix(prefix)
        return (
            f"Đã {action_label} của {project_id}: {detail} "
            f"Trạng thái hiện tại: {state}, state_version {version}."
        )

    if result.status == "already-processed":
        return (
            f"Thao tác {action_label} cho {project_id} đã được ghi nhận trước đó. "
            f"Trạng thái hiện tại: {state}."
        )

    if result.status in {"stale", "expired"}:
        age = "hết hạn" if result.status == "expired" else "đã cũ"
        return f"Thẻ của {project_id} {age}; hãy bấm Refresh rồi thử lại."

    if result.status == "blocked":
        return (
            f"Chưa thể {action_label} {project_id}: checklist hoặc P0/P1 chưa hoàn tất. "
            "Hãy xử lý gate rồi bấm Refresh."
        )

    if result.status == "unauthorized":
        return f"Bạn không có quyền {action_label} {project_id}."

    if result.status in {"unknown", "unavailable"}:
        return f"Thẻ của {project_id} không khả dụng. Dùng lệnh dự phòng: {result.fallback_command}"

    return result.message_vi or f"Không thể xử lý thao tác {action_label} cho {project_id}."


def run_component_action(text: str) -> dict[str, str]:
    """Apply one strict inbound component envelope through durable state and coordinator."""
    envelope = parse_component_action_json(text)
    from openclaw_web.db import Repository, connect, migrate
    from openclaw_web.runtime_adapters import RepositoryComponents

    connection = connect(state_db())
    try:
        migrate(connection)
        repository = Repository(connection)
        service = ComponentActionService(
            RepositoryComponents(repository),
            CoordinatorActions(
                WorkflowCoordinatorAdapter(workflow_root() / "workflow-coordinator.py"), repository
            ),
            state_version=repository.get_project_state_version,
        )
        result = service.execute_envelope(envelope)
        return {
            "status": result.status,
            "message_vi": result.message_vi,
            "fallback_command": result.fallback_command,
        }
    finally:
        connection.close()


def run_component_callback(text: str) -> dict[str, str]:
    """Resolve and execute a trusted Discord callback through durable message identity."""
    payload = strict_callback_json(text)
    from openclaw_web.db import Repository, connect, migrate
    from openclaw_web.runtime_adapters import RepositoryComponents

    connection = connect(state_db())
    try:
        migrate(connection)
        repository = Repository(connection)
        stored = repository.get_component_set_by_message_id(payload["message_id"])
        if stored is None:
            return {
                "status": "unknown",
                "message_vi": "Thẻ này không còn hợp lệ; hãy dùng lệnh dự phòng.",
                "fallback_command": "/lead-approve <project_id>",
            }
        component = component_record(stored)
        envelope = canonical_callback_envelope(payload, component)
        if envelope is None:
            return {
                "status": "stale",
                "message_vi": "Thẻ đã cũ; hãy làm mới trước khi thao tác.",
                "fallback_command": "/lead-approve <project_id>",
            }
        service = ComponentActionService(
            RepositoryComponents(repository),
            CoordinatorActions(
                WorkflowCoordinatorAdapter(workflow_root() / "workflow-coordinator.py"), repository
            ),
            state_version=repository.get_project_state_version,
            gate_ready=component_gate_ready,
            read_only_action=component_read_only,
        )
        result = service.execute_envelope(envelope)
        try:
            project = workflow_project(component)
        except RuntimeError:
            project = None
        return {
            "status": result.status,
            "message_vi": component_result_message(envelope, result, project),
            "fallback_command": result.fallback_command,
        }
    finally:
        connection.close()


__all__ = [
    "DEFAULT_DISCORD_GUILD_ID",
    "CoordinatorActions",
    "bounded_project_value",
    "canonical_callback_envelope",
    "component_gate_ready",
    "component_read_only",
    "component_result_message",
    "has_unresolved_priority",
    "project_snapshot_text",
    "run_component_action",
    "run_component_callback",
    "strict_callback_json",
    "workflow_project",
]
