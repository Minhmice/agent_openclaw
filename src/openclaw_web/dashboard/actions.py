"""Authenticated dashboard actions routed through the workflow coordinator."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from pydantic import ValidationError

from openclaw_web.db.repository import DashboardActionReceipt, Repository, RepositoryConflict
from openclaw_web.models import FeedbackEvent, ProjectState

from .auth import TokenAuthenticator
from .models import ActionRequest, ActionResponse, DashboardAction
from .read_repository import DashboardReadRepository


class DashboardActionError(RuntimeError):
    """Sanitized error that the HTTP server can map to an API status."""

    def __init__(self, code: str, message_vi: str, status_code: int) -> None:
        self.code = code
        self.message_vi = message_vi
        self.status_code = status_code
        super().__init__(message_vi)


class DashboardCoordinator(Protocol):
    def apply(self, request: ActionRequest, actor_id: str) -> Mapping[str, object]: ...


class FeedbackSink(Protocol):
    def __call__(self, request: ActionRequest, response: ActionResponse, actor_id: str) -> object: ...


class DashboardFeedbackRecorder:
    """Write one sanitized, idempotent dashboard feedback event."""

    def __init__(self, repository: Repository, read_repository: DashboardReadRepository) -> None:
        self.repository = repository
        self.read_repository = read_repository

    def __call__(self, request: ActionRequest, response: ActionResponse, actor_id: str) -> None:
        lead = self.read_repository.get_lead(request.target_id)
        project_id = lead.project_id if lead is not None else None
        if project_id is None:
            row = self.repository.connection.execute(
                "SELECT project_id FROM projects WHERE project_id = ?",
                (request.target_id,),
            ).fetchone()
            project_id = None if row is None else str(row["project_id"])
        if project_id is None:
            return
        project_state: ProjectState | None
        try:
            project_state = ProjectState(response.state)
        except ValueError:
            project_state = None
        event = FeedbackEvent(
            event_id=f"dashboard-feedback-{uuid.uuid5(uuid.NAMESPACE_URL, request.idempotency_key).hex}",
            event_type="dashboard-action",
            project_id=project_id,
            actor_id=actor_id,
            action=request.action.value,
            created_at=datetime.now(UTC),
            project_state=project_state,
            state_version=response.state_version,
            payload={
                "source": "dashboard",
                "target_id": request.target_id,
                "state": response.state,
            },
        )
        try:
            self.repository.append_feedback(event)
        except RepositoryConflict:
            # A retry after a crash may reach the recorder after the first
            # attempt already wrote the same event identity.
            return


_FALLBACK_STATES: Mapping[DashboardAction, str] = {
    DashboardAction.SELECT_LEAD: "selected",
    DashboardAction.WATCH_LEAD: "watching",
    DashboardAction.LEAD_APPROVE: "approved",
    DashboardAction.LEAD_REJECT: "rejected",
    DashboardAction.LEAD_REQUEST_CHANGE: "awaiting-command",
    DashboardAction.PAGE_STATUS: "in-progress",
    DashboardAction.PAGE_DONE: "completed",
    DashboardAction.PAGE_APPROVE: "approved",
    DashboardAction.BLOCK: "blocked",
    DashboardAction.FINAL_CONFIRM: "completed",
}
_ALLOWED_RESPONSE_STATES = frozenset(
    {
        "ranked",
        "selected",
        "watching",
        "rejected",
        "awaiting-command",
        "approved",
        "in-progress",
        "completed",
        "blocked",
        "review",
        "website-brief",
        "task",
        "stakeholder-review",
        "offer-ready",
    }
)
_SAFE_NEXT_ACTION = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_SAFE_FAILURE_CODES = frozenset({"stale_state", "action_failed"})
_FAILURE_STATUS = {"stale_state": 409, "action_failed": 503}
_FAILURE_MESSAGES = {
    "stale_state": "Snapshot đã cũ; hãy tải lại trước khi thao tác.",
    "action_failed": "Không thể hoàn tất action.",
}
_LEAD_ACTIONS = frozenset(
    {
        DashboardAction.SELECT_LEAD,
        DashboardAction.WATCH_LEAD,
        DashboardAction.LEAD_APPROVE,
        DashboardAction.LEAD_REJECT,
        DashboardAction.LEAD_REQUEST_CHANGE,
    }
)
_SENSITIVE_MESSAGE = re.compile(
    r"(?:authorization|api[_-]?key|password|private[_-]?key|secret|session|cookie|token)\s*[:=]",
    re.IGNORECASE,
)


class DashboardActionService:
    """Validate, claim, route and receipt one dashboard action."""

    def __init__(
        self,
        *,
        repository: Repository,
        read_repository: DashboardReadRepository,
        authenticator: TokenAuthenticator,
        coordinator: object | None,
        feedback_sink: FeedbackSink | None = None,
    ) -> None:
        self.repository = repository
        self.read_repository = read_repository
        self.authenticator = authenticator
        self.coordinator = coordinator
        self.feedback_sink = feedback_sink

    def _authenticate(self, authorization: str | None) -> str:
        actor = self.authenticator.authenticate(authorization)
        if actor is None:
            raise DashboardActionError("unauthorized", "Bearer token không hợp lệ.", 401)
        return actor

    @staticmethod
    def _parse_request(payload: Mapping[str, Any]) -> ActionRequest:
        try:
            return ActionRequest.model_validate(payload)
        except ValidationError as error:
            # Do not return pydantic internals or request content to a remote caller.
            raise DashboardActionError("invalid_action", "Action dashboard không hợp lệ.", 400) from error

    def _assert_permission(self, actor: str, request: ActionRequest) -> None:
        if not self.authenticator.allows(actor, request.action):
            raise DashboardActionError("forbidden", "Actor không có quyền thực hiện action này.", 403)

    def _current_state(self, target_id: str) -> tuple[str, int]:
        lead = self.read_repository.get_lead(target_id)
        if lead is not None:
            if lead.project_id is not None and lead.project_state_version is not None:
                return lead.state, lead.project_state_version
            return lead.state, lead.state_version
        try:
            version = self.repository.get_project_state_version(target_id)
        except KeyError as error:
            raise DashboardActionError("target_not_found", "Không tìm thấy target.", 404) from error
        row = self.repository.connection.execute(
            "SELECT state FROM projects WHERE project_id = ?", (target_id,)
        ).fetchone()
        if row is None:
            raise DashboardActionError("target_not_found", "Không tìm thấy target.", 404)
        state = str(row["state"])
        return (state if state in _ALLOWED_RESPONSE_STATES else "unavailable"), version

    def _coordinator_target(self, request: ActionRequest) -> str:
        """Resolve a portfolio entry/candidate target to its workflow project.

        Test and embedded coordinators may intentionally consume the public
        target as supplied. The subprocess adapter is the boundary that needs
        the canonical project directory used by the workflow coordinator.
        """

        if request.action not in _LEAD_ACTIONS:
            return request.target_id
        lead = self.read_repository.get_lead(request.target_id)
        if lead is not None and lead.project_id is not None:
            return lead.project_id
        return request.target_id

    @staticmethod
    def _replay_or_none(receipt: DashboardActionReceipt) -> ActionResponse | None:
        if receipt.status != "succeeded" or receipt.response is None:
            return None
        try:
            return ActionResponse.model_validate(receipt.response)
        except ValidationError as error:
            raise DashboardActionError("receipt_invalid", "Receipt action không hợp lệ.", 500) from error

    def _invoke_coordinator(self, request: ActionRequest, actor: str) -> Mapping[str, object]:
        coordinator = self.coordinator
        if coordinator is None:
            raise DashboardActionError("coordinator_unavailable", "Workflow coordinator chưa sẵn sàng.", 503)
        try:
            result: object
            if callable(coordinator) and not hasattr(coordinator, "apply"):
                callback = cast(Callable[[ActionRequest, str], object], coordinator)
                result = callback(request, actor)
            elif hasattr(coordinator, "apply"):
                callback = cast(Callable[[ActionRequest, str], object], cast(Any, coordinator).apply)
                result = callback(request, actor)
            elif hasattr(coordinator, "action") and hasattr(coordinator, "execute"):
                action_name = {
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
                }[request.action]
                action = cast(Callable[..., object], cast(Any, coordinator).action)
                execute = cast(Callable[[object], object], cast(Any, coordinator).execute)
                command = action(
                    project_id=self._coordinator_target(request),
                    action=action_name,
                    actor_id=actor,
                    page_slug=request.page_slug,
                    reason=request.reason,
                )
                completed = execute(command)
                if getattr(completed, "returncode", 1) != 0:
                    raise DashboardActionError("coordinator_rejected", "Coordinator từ chối action.", 409)
                output = getattr(completed, "stdout", "")
                if not isinstance(output, str | bytes | bytearray):
                    raise DashboardActionError("coordinator_invalid", "Coordinator trả về dữ liệu không hợp lệ.", 503)
                try:
                    parsed = json.loads(output) if output else {}
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise DashboardActionError("coordinator_invalid", "Coordinator trả về dữ liệu không hợp lệ.", 503) from error
                result = parsed if isinstance(parsed, Mapping) else {}
            else:
                raise DashboardActionError("coordinator_unavailable", "Workflow coordinator chưa sẵn sàng.", 503)
        except DashboardActionError:
            raise
        except Exception as error:
            raise DashboardActionError("coordinator_unavailable", "Workflow coordinator không phản hồi.", 503) from error
        if not isinstance(result, Mapping):
            raise DashboardActionError("coordinator_invalid", "Coordinator trả về kết quả không hợp lệ.", 503)
        if result.get("status") in {"failed", "error"}:
            raise DashboardActionError("coordinator_rejected", "Coordinator từ chối action.", 409)
        return result

    def execute(self, payload: Mapping[str, Any], authorization: str | None) -> ActionResponse:
        actor = self._authenticate(authorization)
        actor_id = self.authenticator.actor_id(actor)
        request = self._parse_request(payload)
        self._assert_permission(actor, request)
        try:
            receipt = self.repository.claim_dashboard_action(
                idempotency_key=request.idempotency_key,
                action=request.action.value,
                target_id=request.target_id,
                actor_id=actor_id,
                expected_state_version=request.expected_state_version,
            )
        except RepositoryConflict as error:
            raise DashboardActionError(
                "idempotency_conflict",
                "Idempotency key đã được dùng cho action khác.",
                409,
            ) from error
        except Exception as error:
            if isinstance(error, DashboardActionError):
                raise
            raise DashboardActionError("receipt_unavailable", "Không thể tạo action receipt.", 503) from error

        replay = self._replay_or_none(receipt)
        if replay is not None:
            return replay
        if receipt.status == "failed":
            code = receipt.error_code if receipt.error_code in _SAFE_FAILURE_CODES else "action_failed"
            raise DashboardActionError(code, _FAILURE_MESSAGES[code], _FAILURE_STATUS[code])

        try:
            current_state, current_version = self._current_state(request.target_id)
            if current_version != request.expected_state_version:
                stale_response = {
                    "status": "conflict",
                    "event_id": receipt.event_id,
                    "target_id": request.target_id,
                    "state": current_state,
                    "state_version": current_version,
                    "next_action": None,
                    "message_vi": "Snapshot đã cũ; hãy tải lại trước khi thao tác.",
                }
                self.repository.complete_dashboard_action(
                    request.idempotency_key,
                    status="failed",
                    response=stale_response,
                    error_code="stale_state",
                )
                raise DashboardActionError("stale_state", str(stale_response["message_vi"]), 409)

            coordinator_result = self._invoke_coordinator(request, actor_id)
            raw_state = coordinator_result.get("state")
            state = (
                current_state
                if request.action is DashboardAction.PAGE_STATUS
                else raw_state
                if isinstance(raw_state, str) and raw_state in _ALLOWED_RESPONSE_STATES
                else _FALLBACK_STATES[request.action]
            )
            raw_version = coordinator_result.get("state_version")
            if request.action is DashboardAction.PAGE_STATUS:
                state_version = current_version
            elif raw_version is None:
                state_version = current_version + 1
            else:
                if (
                    isinstance(raw_version, bool)
                    or not isinstance(raw_version, int)
                    or raw_version != current_version + 1
                ):
                    raise DashboardActionError(
                        "coordinator_invalid", "Coordinator trả về kết quả không hợp lệ.", 503
                    )
                state_version = raw_version
            raw_next = coordinator_result.get("next_action")
            next_action = (
                raw_next
                if isinstance(raw_next, str) and _SAFE_NEXT_ACTION.fullmatch(raw_next)
                else None
            )
            raw_message = coordinator_result.get("message_vi")
            message = (
                raw_message.strip()[:500]
                if isinstance(raw_message, str)
                and raw_message.strip()
                and not _SENSITIVE_MESSAGE.search(raw_message)
                else "Đã tiếp nhận action."
            )
            response = ActionResponse(
                status="accepted",
                event_id=receipt.event_id,
                target_id=request.target_id,
                state=state,
                state_version=state_version,
                next_action=next_action,
                message_vi=message,
            )
            self.repository.complete_dashboard_action(
                request.idempotency_key,
                status="succeeded",
                response=response.model_dump(mode="json"),
            )
            if self.feedback_sink is not None:
                try:
                    self.feedback_sink(request, response, actor_id)
                except Exception as feedback_error:  # noqa: BLE001 - receipt is already durable
                    _ = feedback_error
            return response
        except DashboardActionError:
            raise
        except Exception as error:
            try:
                self.repository.complete_dashboard_action(
                    request.idempotency_key,
                    status="failed",
                    error_code="action_failed",
                )
            except Exception as cleanup_error:  # noqa: BLE001 - preserve the original action error
                _ = cleanup_error
            raise DashboardActionError("action_failed", "Không thể hoàn tất action.", 503) from error


__all__ = [
    "DashboardActionError",
    "DashboardActionService",
    "DashboardCoordinator",
    "DashboardFeedbackRecorder",
]
