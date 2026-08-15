"""Small production composition roots for CLI commands and systemd units."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from openclaw_web.audit.lighthouse import LighthouseRunner
from openclaw_web.crawl import WebsiteCrawler, normalize_url, resolve_and_validate
from openclaw_web.db import Repository, connect, migrate
from openclaw_web.db.repository import ReviewProjectRecord
from openclaw_web.delivery.components import (
    DEFAULT_REVIEW_REJECTION_REASON,
    ActionResult,
    ComponentActionEnvelope,
    ComponentActionService,
    ComponentSetRecord,
    REVIEW_CARD_VERSION,
    build_review_card,
    component_record,
    parse_component_action_json,
)
from openclaw_web.delivery.openclaw_transport import OpenClawAgentTransport
from openclaw_web.delivery.outbox import OutboxWorker
from openclaw_web.delivery.workflow_adapter import WorkflowCoordinatorAdapter
from openclaw_web.discovery.base import AutomaticDiscoveryProvider, DiscoveryError
from openclaw_web.discovery.overpass import OverpassDiscoverySource
from openclaw_web.discovery.places import GooglePlacesDiscoverySource
from openclaw_web.discovery.serper import SerperDiscoverySource
from openclaw_web.models import DeliveryRecord, DeliveryState, ProjectState
from openclaw_web.pipeline.cron import CronResult, CronRunner
from openclaw_web.pipeline.production import ProductionPipeline, ProductionProject
from openclaw_web.review.legacy import (
    LegacyReviewError,
    load_legacy_review_project,
    render_legacy_review_message,
)
from openclaw_web.scoring.rules import Rubric, load_rubric
from openclaw_web.screenshots import ScreenshotRunner
from openclaw_web.settings import MarketConfig, load_market

DEFAULT_DISCORD_GUILD_ID = "1446612692910739637"
DEFAULT_DISCORD_REVIEW_CHANNEL_ID = "1536658476288450630"


def _state_db() -> Path:
    configured = os.environ.get("OPENCLAW_WEB_STATE_DB")
    return Path(configured).expanduser() if configured else Path.home() / ".local/state/openclaw-web/state.sqlite"


def _discord_guild_id() -> str:
    configured = os.environ.get("OPENCLAW_WEB_DISCORD_GUILD_ID", "")
    if not configured:
        raise RuntimeError("OPENCLAW_WEB_DISCORD_GUILD_ID is not configured")
    return configured


def _legacy_artifact_root(override: Path | None) -> Path:
    if override is not None:
        return override
    configured = os.environ.get("OPENCLAW_WEB_ARTIFACT_ROOT")
    if configured:
        return Path(configured).expanduser() / "legacy"
    return Path.home() / ".local/share/openclaw-web/legacy-artifacts"


def _legacy_created_at(
    project: dict[str, object], repository: Repository, project_id: str, fallback: datetime
) -> datetime:
    raw = project.get("created_at")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed.astimezone(UTC)
        except ValueError:
            pass
    row = repository.connection.execute(
        "SELECT snapshot_json FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone()
    if row is not None:
        try:
            persisted = json.loads(str(row["snapshot_json"]))
            persisted_created = persisted.get("created_at") if isinstance(persisted, dict) else None
            if isinstance(persisted_created, str):
                parsed = datetime.fromisoformat(persisted_created)
                if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                    return parsed.astimezone(UTC)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return fallback


def run_legacy_review(
    project_id: str,
    *,
    workflow_root: Path | None = None,
    review_channel: str | None = None,
    guild_id: str | None = None,
    artifact_root: Path | None = None,
) -> dict[str, object]:
    """Bridge one legacy Curie project into the durable native-card outbox."""

    root = workflow_root or _workflow_root()
    project_dir = root / "projects" / project_id
    project, dossier = load_legacy_review_project(project_dir, project_id)
    channel = (
        review_channel
        or os.environ.get("OPENCLAW_WEB_REVIEW_CHANNEL")
        or os.environ.get("OPENCLAW_WEB_REVIEW_CHANNEL_ID")
        or DEFAULT_DISCORD_REVIEW_CHANNEL_ID
    ).strip()
    guild = (guild_id or os.environ.get("OPENCLAW_WEB_DISCORD_GUILD_ID") or DEFAULT_DISCORD_GUILD_ID).strip()
    if not channel or not channel.isdigit() or not guild or not guild.isdigit():
        raise LegacyReviewError("Discord review identity is not configured")
    business_name = project.get("business_name")
    website = project.get("website")
    if not isinstance(business_name, str) or not business_name.strip():
        raise LegacyReviewError("legacy project business_name is missing")
    if not isinstance(website, str) or not website.strip():
        raise LegacyReviewError("legacy project website is missing")
    message = render_legacy_review_message(project, dossier)
    now = datetime.now(UTC)
    component_set_id = f"component-{uuid.uuid5(uuid.NAMESPACE_URL, f'component:{project_id}:review:{REVIEW_CARD_VERSION}').hex}"
    expires_at = now + timedelta(hours=24)
    artifact_dir = _legacy_artifact_root(artifact_root) / project_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload_path = artifact_dir / "review-card.json"
    payload = {
        "component_set": {
            "component_set_id": component_set_id,
            "project_id": project_id,
            "card_type": "review",
            "allowed_actions": [button.action for button in build_review_card(project_id).buttons],
            "expires_at": expires_at.isoformat(),
            "state_version": 0,
            "project_state": ProjectState.REVIEW.value,
        },
        "message": message,
        "components": build_review_card(project_id).payload,
    }
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    connection = connect(_state_db())
    try:
        migrate(connection)
        repository = Repository(connection)
        candidate = repository.upsert_candidate(str(website), str(business_name), None)
        created_at = _legacy_created_at(project, repository, project_id, now)
        repository.ensure_review_project(
            _MutableReviewProject(
                project_id=project_id,
                candidate_id=candidate.candidate_id,
                market_id="hanoi-80km",
                artifact_dir=str(artifact_dir),
                created_at=created_at,
            )
        )
        delivery_key = f"review:{project_id}:{REVIEW_CARD_VERSION}"
        delivery_id = f"delivery-{uuid.uuid5(uuid.NAMESPACE_URL, f'delivery:{project_id}:review:{REVIEW_CARD_VERSION}').hex}"
        delivery = DeliveryRecord(
            delivery_id=delivery_id,
            event_type="review-card",
            project_id=project_id,
            channel_id=channel,
            payload_path=str(payload_path),
            idempotency_key=delivery_key,
            status=DeliveryState.PENDING,
        )
        existing_status = repository.get_delivery_status(delivery.idempotency_key)
        if existing_status is DeliveryState.SENT:
            existing = repository.get_delivery(delivery.delivery_id)
            return {
                "project_id": project_id,
                "delivery_id": existing.delivery_id,
                "component_set_id": component_set_id,
                "status": existing.status.value,
                "message_id": existing.message_id,
                "message_url": str(existing.message_url) if existing.message_url is not None else None,
            }
        repository.enqueue_delivery_once(delivery)
        sent = OutboxWorker(
            repository,
            OpenClawAgentTransport(guild_id=guild),
        ).dispatch_once()
        persisted = repository.get_delivery(delivery.delivery_id)
        if sent is None or persisted.status is not DeliveryState.SENT:
            raise LegacyReviewError(
                f"legacy review delivery did not reach sent state: {persisted.last_error or persisted.status.value}"
            )
        return {
            "project_id": project_id,
            "delivery_id": persisted.delivery_id,
            "component_set_id": component_set_id,
            "status": persisted.status.value,
            "message_id": persisted.message_id,
            "message_url": str(persisted.message_url) if persisted.message_url is not None else None,
        }
    finally:
        connection.close()


DiscoveryComposition = Callable[[], str]


class _SqliteRunLocks:
    """Lock repository with one SQLite connection per calling thread."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _apply(self, method: str, *args: object) -> object:
        connection = connect(self.path)
        try:
            migrate(connection)
            return getattr(Repository(connection), method)(*args)
        finally:
            connection.close()

    def acquire_run_lock(
        self, key: str, owner: str, now: datetime, lease_seconds: int
    ) -> bool:
        return bool(self._apply("acquire_run_lock", key, owner, now, lease_seconds))

    def renew_run_lock(
        self, key: str, owner: str, now: datetime, lease_seconds: int
    ) -> bool:
        return bool(self._apply("renew_run_lock", key, owner, now, lease_seconds))

    def release_run_lock(self, key: str, owner: str) -> None:
        self._apply("release_run_lock", key, owner)


class _RepositoryComponents:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def get_component_set(
        self, channel_id: str, message_id: str
    ) -> ComponentSetRecord | None:
        stored = self.repository.get_component_set(channel_id, message_id)
        return None if stored is None else component_record(stored)

    def claim_component_action(self, component_set_id: str, actor_id: str, action: str) -> bool:
        claimed = self.repository.claim_component_action(component_set_id, actor_id, action)
        if not claimed:
            self.repository.reconcile_component_action(component_set_id, actor_id, action)
        return claimed

    def release_component_action(self, component_set_id: str, actor_id: str, action: str) -> None:
        self.repository.release_component_action(component_set_id, actor_id, action)


class _RepositoryProduction:
    """Narrow production-pipeline adapters over the durable repository."""

    def __init__(self, repository: Repository, workflow_root: Path) -> None:
        self.repository = repository
        self.workflow_root = workflow_root

    def persist_project(self, project: ProductionProject) -> bool:
        inserted = self.repository.ensure_review_project(
            _MutableReviewProject(
                project_id=project.project_id,
                candidate_id=project.candidate_id,
                market_id=project.market_id,
                artifact_dir=project.artifact_dir,
                created_at=project.created_at,
            )
        )
        source = Path(project.artifact_dir) / "workflow-project.json"
        coordinator = self.workflow_root / "workflow-coordinator.py"
        target = self.workflow_root / "projects" / project.project_id / "project.json"
        if not target.is_file():
            completed = WorkflowCoordinatorAdapter(coordinator).initialize(source)
            if completed.returncode != 0:
                raise RuntimeError("workflow coordinator rejected project initialization")
        return inserted

    def enqueue_delivery(self, delivery: DeliveryRecord) -> bool:
        return self.repository.enqueue_delivery_once(delivery)


@dataclass(slots=True)
class _MutableReviewProject(ReviewProjectRecord):
    project_id: str
    candidate_id: str
    market_id: str
    artifact_dir: str
    created_at: datetime


class _CoordinatorActions:
    def __init__(self, adapter: WorkflowCoordinatorAdapter, repository: Repository) -> None:
        self.adapter = adapter
        self.repository = repository

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
                _workflow_root()
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


_CALLBACK_KEYS = frozenset({"actor_id", "guild_id", "message_id", "value"})
_DISCORD_SNOWFLAKE = re.compile(r"^[0-9]{17,20}$")


def _strict_callback_json(text: str) -> dict[str, str]:
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
    configured_guild = os.environ.get(
        "OPENCLAW_WEB_DISCORD_GUILD_ID", DEFAULT_DISCORD_GUILD_ID
    )
    if result["guild_id"] != configured_guild:
        raise ValueError("invalid component callback envelope")
    return result


def _canonical_callback_envelope(
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


def _workflow_project(component: ComponentSetRecord) -> dict[str, object]:
    path = _workflow_root() / "projects" / component.project_id / "project.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise RuntimeError("workflow project artifact is unavailable") from error
    if not isinstance(payload, dict) or payload.get("project_id") != component.project_id:
        raise RuntimeError("workflow project artifact is invalid")
    return payload


def _has_unresolved_priority(page: object) -> bool:
    return isinstance(page, dict) and (
        page.get("unresolved_priority") in {"P0", "P1"}
        or page.get("unresolved_p0") is True
        or page.get("unresolved_p1") is True
    )


def _component_gate_ready(component: ComponentSetRecord) -> bool:
    if not set(component.allowed_actions) & {"approve", "final-confirm", "page-approve"}:
        return True
    project = _workflow_project(component)
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
        return checklist_ready and not _has_unresolved_priority(page)
    if any(_has_unresolved_priority(page) for page in pages):
        return False
    if component.card_type == "final":
        return bool(pages) and all(
            isinstance(page, dict) and page.get("status") == "approved" for page in pages
        )
    return True


def _component_read_only(component: ComponentSetRecord, action: str) -> str:
    project = _workflow_project(component)
    if action == "refresh":
        return (
            f"Project {component.project_id}: trạng thái {project.get('status', 'unknown')}, "
            f"state_version {project.get('state_version', 'unknown')}."
        )
    if action == "view-unresolved":
        pages = project.get("pages")
        count = sum(1 for page in pages if _has_unresolved_priority(page)) if isinstance(pages, list) else 0
        return f"Project {component.project_id}: còn {count} page có P0/P1 chưa xử lý."
    if action == "view-evidence":
        artifact_dir = project.get("artifact_dir")
        available: list[str] = []
        if isinstance(artifact_dir, str) and artifact_dir:
            evidence = Path(artifact_dir) / "evidence"
            available = sorted(path.name for path in evidence.glob("*.json") if path.is_file())
        if not available:
            project_dir = (_workflow_root() / "projects" / component.project_id).resolve()
            workflow_root = _workflow_root().resolve()
            if project_dir.parent != workflow_root / "projects":
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


def _project_snapshot_text(project: dict[str, object] | None) -> tuple[str, str]:
    if project is None:
        return "unknown", "unknown"
    state = project.get("status", project.get("state", "unknown"))
    version = project.get("state_version", "unknown")
    state_text = str(state).strip() or "unknown"
    version_text = str(version).strip() or "unknown"
    return state_text[:80], version_text[:40]


def _bounded_project_value(
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


def _component_result_message(
    envelope: ComponentActionEnvelope,
    result: ActionResult,
    project: dict[str, object] | None,
) -> str:
    """Render one bounded Vietnamese callback result without exposing internals."""

    project_id = envelope.project_id
    state, version = _project_snapshot_text(project)
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
            return (
                f"Đã duyệt {project_id}. Trạng thái mới: {state}. "
                "Bước tiếp theo: Website Brief."
            )
        if envelope.action == "reject":
            reason = _bounded_project_value(
                project,
                ("rejection_reason", "discard_reason"),
                DEFAULT_REVIEW_REJECTION_REASON,
            )
            return (
                f"Đã từ chối {project_id}. Lý do: {reason} "
                f"Trạng thái mới: {state}."
            )
        return f"Đã ghi nhận thao tác {action_label} cho {project_id}. Trạng thái mới: {state}."

    if result.status == "read-only":
        if envelope.action == "refresh":
            return f"Refresh {project_id}: trạng thái {state}, state_version {version}."
        detail = result.message_vi
        prefix = f"Project {project_id}: "
        if detail.startswith(prefix):
            detail = detail[len(prefix) :]
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
        return (
            f"Thẻ của {project_id} không khả dụng. "
            f"Dùng lệnh dự phòng: {result.fallback_command}"
        )

    return result.message_vi or f"Không thể xử lý thao tác {action_label} cho {project_id}."


def _workflow_root() -> Path:
    configured = os.environ.get("OPENCLAW_WORKFLOW_ROOT")
    return Path(configured).expanduser() if configured else Path.home() / ".openclaw/workflow"


def run_component_action(text: str) -> dict[str, str]:
    """Apply one strict inbound component envelope through durable state and coordinator."""

    envelope = parse_component_action_json(text)
    connection = connect(_state_db())
    try:
        migrate(connection)
        repository = Repository(connection)
        service = ComponentActionService(
            _RepositoryComponents(repository),
            _CoordinatorActions(
                WorkflowCoordinatorAdapter(_workflow_root() / "workflow-coordinator.py"),
                repository,
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
    """Resolve a trusted Discord callback by durable message identity, then execute it.

    OpenClaw authorizes the Discord component before invoking the plugin bridge;
    this durable layer remains the authority for actor, message, state, and
    action checks. Keep that distinction and the plugin's bounded rejection
    diagnostics (including error-level reason codes) aligned across the
    release pair.
    """

    payload = _strict_callback_json(text)
    connection = connect(_state_db())
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
        envelope = _canonical_callback_envelope(payload, component)
        if envelope is None:
            return {
                "status": "stale",
                "message_vi": "Thẻ đã cũ; hãy làm mới trước khi thao tác.",
                "fallback_command": "/lead-approve <project_id>",
            }
        service = ComponentActionService(
            _RepositoryComponents(repository),
            _CoordinatorActions(
                WorkflowCoordinatorAdapter(_workflow_root() / "workflow-coordinator.py"),
                repository,
            ),
            state_version=repository.get_project_state_version,
            gate_ready=_component_gate_ready,
            read_only_action=_component_read_only,
        )
        result = service.execute_envelope(envelope)
        try:
            project = _workflow_project(component)
        except RuntimeError:
            project = None
        return {
            "status": result.status,
            "message_vi": _component_result_message(envelope, result, project),
            "fallback_command": result.fallback_command,
        }
    finally:
        connection.close()


CompositionReadiness = Literal[
    "artifact_root_not_absolute",
    "artifact_root_unavailable",
    "market_config_unavailable",
    "scoring_config_unavailable",
    "rubric_config_unavailable",
    "review_channel_not_configured",
    "ready",
]


def _configured_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


@dataclass(slots=True)
class ProductionDiscoveryComposition:
    """Production boundary composed only from bounded, repository-backed services."""

    artifact_root: Path
    market_config: Path
    scoring_config: Path
    market: MarketConfig | None = None
    rubric: Rubric | None = None
    review_channel: str | None = None
    providers: tuple[AutomaticDiscoveryProvider, ...] = ()

    @classmethod
    def from_environment(cls) -> ProductionDiscoveryComposition:
        root = Path(__file__).resolve().parents[2]
        artifact_root = _configured_path(
            "OPENCLAW_WEB_ARTIFACT_ROOT",
            Path.home() / ".local/share/openclaw-web/artifacts",
        )
        market_config = _configured_path(
            "OPENCLAW_WEB_MARKET_CONFIG", root / "config/markets/hanoi-80km.yaml"
        )
        scoring_config = _configured_path(
            "OPENCLAW_WEB_SCORING_CONFIG", root / "config/scoring/base-v1.yaml"
        )
        composition = cls(artifact_root, market_config, scoring_config)
        if not artifact_root.is_absolute() or not artifact_root.is_dir():
            return composition
        try:
            market = load_market(market_config)
            rubric = load_rubric(scoring_config)
        except (OSError, ValueError):
            return composition
        providers: list[AutomaticDiscoveryProvider] = [OverpassDiscoverySource()]
        try:
            if key := os.environ.get("SERPER_API_KEY"):
                providers.append(SerperDiscoverySource(key))
            if key := os.environ.get("GOOGLE_PLACES_API_KEY"):
                providers.append(GooglePlacesDiscoverySource(key))
        except DiscoveryError:
            return composition
        composition.market = market
        composition.rubric = rubric
        review_channel = os.environ.get(
            "OPENCLAW_WEB_REVIEW_CHANNEL",
            os.environ.get("OPENCLAW_WEB_REVIEW_CHANNEL_ID", ""),
        ).strip()
        composition.review_channel = review_channel or None
        composition.providers = tuple(providers)
        return composition

    def readiness(self) -> CompositionReadiness:
        if not self.artifact_root.is_absolute():
            return "artifact_root_not_absolute"
        if not self.artifact_root.is_dir():
            return "artifact_root_unavailable"
        if self.market is None or not self.market_config.is_file():
            return "market_config_unavailable"
        if self.rubric is None or not self.scoring_config.is_file():
            return "scoring_config_unavailable"
        if not self.review_channel:
            return "review_channel_not_configured"
        return "ready"

    @staticmethod
    async def _screenshot_url_validator(_source: str | None, target: str) -> str:
        canonical = normalize_url(target)
        host = urlsplit(canonical).hostname
        if host is None:
            raise ValueError("screenshot URL is missing a host")
        resolve_and_validate(host, _resolve)
        return canonical

    async def _run(self) -> str:
        if self.readiness() != "ready":
            return "failed"
        assert self.market is not None
        assert self.rubric is not None
        assert self.review_channel is not None
        connection = connect(_state_db())
        crawler = WebsiteCrawler()
        try:
            migrate(connection)
            repository = Repository(connection)
            durable = _RepositoryProduction(repository, _workflow_root())
            screenshot = ScreenshotRunner(
                self.artifact_root / ".screenshot-work",
                url_validator=self._screenshot_url_validator,
            )
            lighthouse_runner = LighthouseRunner(self.artifact_root / ".lighthouse-work")

            pipeline = ProductionPipeline(
                repository=repository,
                artifact_root=self.artifact_root,
                market=self.market,
                providers=self.providers,
                crawler=crawler.crawl,
                rubric=self.rubric,
                review_channel=self.review_channel,
                clock=lambda: datetime.now(UTC),
                project_sink=durable.persist_project,
                delivery_sink=durable.enqueue_delivery,
                screenshot=screenshot.capture,
                lighthouse=lighthouse_runner.run,
            )
            result = await pipeline.run()
            if result.status != "candidate-posted" or result.project_id is None:
                return result.status
            key = f"review:{result.project_id}"
            if repository.get_delivery_status(key) is DeliveryState.SENT:
                return "candidate-posted"
            delivery = OutboxWorker(
                repository,
                OpenClawAgentTransport(guild_id=_discord_guild_id()),
            ).dispatch_once()
            if delivery is None or delivery.project_id != result.project_id:
                return "failed"
            return (
                "candidate-posted"
                if repository.get_delivery_status(key) is DeliveryState.SENT
                else "failed"
            )
        except Exception:  # noqa: BLE001 - scheduler has one stable failure contract
            return "failed"
        finally:
            await crawler.aclose()
            for provider in self.providers:
                closer = getattr(provider, "aclose", None)
                if closer is not None:
                    await closer()
            connection.close()

    def __call__(self) -> str:
        """Never convert missing downstream composition into no-candidate evidence."""

        return asyncio.run(self._run())


def _resolve(host: str) -> tuple[str, ...]:
    import socket

    return tuple(str(item[4][0]) for item in socket.getaddrinfo(host, None))


def drain_delivery_outbox(*, maximum: int = 10) -> dict[str, object]:
    """Dispatch at most ``maximum`` persisted delivery records and expose no payload data."""

    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100:
        raise ValueError("maximum must be between 1 and 100")
    connection = connect(_state_db())
    try:
        migrate(connection)
        repository = Repository(connection)
        eligible = repository.connection.execute(
            """
            SELECT 1 FROM deliveries
            WHERE status = 'pending'
               OR (status = 'failed'
                   AND CAST(json_extract(snapshot_json, '$.attempt_count') AS INTEGER) < 2)
            LIMIT 1
            """
        ).fetchone()
        if eligible is None:
            return {"status": "idle", "dispatched": 0, "sent": 0, "failed": 0}
        worker = OutboxWorker(
            repository,
            OpenClawAgentTransport(guild_id=_discord_guild_id()),
        )
        dispatched = 0
        sent = 0
        failed = 0
        for _ in range(maximum):
            record = worker.dispatch_once()
            if record is None:
                break
            dispatched += 1
            if record.status is DeliveryState.SENT:
                sent += 1
            elif record.status is DeliveryState.FAILED:
                failed += 1
        return {
            "status": "sent" if sent else ("failed" if failed else "idle"),
            "dispatched": dispatched,
            "sent": sent,
            "failed": failed,
        }
    finally:
        connection.close()


def run_daily_discovery(
    *,
    discover: DiscoveryComposition | None = None,
    renew_interval_seconds: float | None = None,
) -> CronResult:
    """Run the bounded daily boundary through an injected deterministic composition.

    Production composition belongs at this boundary: provider discovery, audit, scoring,
    delivery, and persistence are ordinary Python services.  An OpenClaw model request is
    deliberately not a scheduler or an orchestration dependency.
    """

    state_db = _state_db()
    connection = connect(state_db)
    try:
        migrate(connection)
        schedule_date = datetime.now(UTC).date()
    finally:
        connection.close()
    return CronRunner(
        _SqliteRunLocks(state_db),
        discover or ProductionDiscoveryComposition.from_environment(),
        renew_interval_seconds=renew_interval_seconds,
    ).run(schedule_date)
