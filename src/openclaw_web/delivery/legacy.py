"""Legacy Curie review bridge capability boundary."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from openclaw_web.delivery.components import REVIEW_CARD_VERSION, build_review_card
from openclaw_web.delivery.openclaw_transport import OpenClawAgentTransport
from openclaw_web.delivery.outbox import OutboxWorker
from openclaw_web.models import DeliveryRecord, DeliveryState, ProjectState
from openclaw_web.review.legacy import (
    LegacyReviewError,
    load_legacy_review_project,
    render_legacy_review_message,
)
from openclaw_web.runtime_support import state_db
from openclaw_web.runtime_support import workflow_root as default_workflow_root

DEFAULT_DISCORD_GUILD_ID = "1446612692910739637"
DEFAULT_DISCORD_REVIEW_CHANNEL_ID = "1536658476288450630"


def legacy_artifact_root(override: Path | None) -> Path:
    if override is not None:
        return override
    configured = os.environ.get("OPENCLAW_WEB_ARTIFACT_ROOT")
    if configured:
        return Path(configured).expanduser() / "legacy"
    return Path.home() / ".local/share/openclaw-web/legacy-artifacts"


def legacy_created_at(
    project: dict[str, object], repository: Any, project_id: str, fallback: datetime
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
    state_db_path: Path | None = None,
    transport_factory: Callable[..., OpenClawAgentTransport] = OpenClawAgentTransport,
) -> dict[str, object]:
    """Bridge one legacy Curie project into the durable native-card outbox."""
    root = workflow_root if workflow_root is not None else default_workflow_root()
    project_dir = root / "projects" / project_id
    project, dossier = load_legacy_review_project(project_dir, project_id)
    channel = (
        review_channel
        or os.environ.get("OPENCLAW_WEB_REVIEW_CHANNEL")
        or os.environ.get("OPENCLAW_WEB_REVIEW_CHANNEL_ID")
        or DEFAULT_DISCORD_REVIEW_CHANNEL_ID
    ).strip()
    guild = (
        guild_id or os.environ.get("OPENCLAW_WEB_DISCORD_GUILD_ID") or DEFAULT_DISCORD_GUILD_ID
    ).strip()
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
    artifact_dir = legacy_artifact_root(artifact_root) / project_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload_path = artifact_dir / "review-card.json"
    card = build_review_card(project_id)
    payload = {
        "component_set": {
            "component_set_id": component_set_id,
            "project_id": project_id,
            "card_type": "review",
            "allowed_actions": [button.action for button in card.buttons],
            "expires_at": expires_at.isoformat(),
            "state_version": 0,
            "project_state": ProjectState.REVIEW.value,
        },
        "message": message,
        "components": card.payload,
    }
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    from openclaw_web.db import Repository, connect, migrate
    from openclaw_web.runtime_adapters import MutableReviewProject

    connection = connect(state_db_path if state_db_path is not None else state_db())
    try:
        migrate(connection)
        repository = Repository(connection)
        candidate = repository.upsert_candidate(str(website), str(business_name), None)
        created_at = legacy_created_at(project, repository, project_id, now)
        repository.ensure_review_project(
            MutableReviewProject(
                project_id=project_id,
                candidate_id=candidate.candidate_id,
                market_id="hanoi-80km",
                artifact_dir=str(artifact_dir),
                created_at=created_at,
            )
        )
        legacy_delivery_key = f"review:{project_id}"
        project_row = connection.execute(
            "SELECT state, state_version FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if (
            project_row is not None
            and (
                str(project_row["state"]) != ProjectState.REVIEW.value
                or int(project_row["state_version"]) != 0
            )
            and repository.get_delivery_status(legacy_delivery_key) is DeliveryState.SENT
        ):
            legacy_delivery_id = (
                f"delivery-{uuid.uuid5(uuid.NAMESPACE_URL, f'delivery:{project_id}:review').hex}"
            )
            existing = repository.get_delivery(legacy_delivery_id)
            existing_component_id = component_set_id
            if existing.message_id is not None:
                old_component = repository.get_component_set(channel, existing.message_id)
                if old_component is not None:
                    existing_component_id = old_component.component_set_id
            return {
                "project_id": project_id,
                "delivery_id": existing.delivery_id,
                "component_set_id": existing_component_id,
                "status": existing.status.value,
                "message_id": existing.message_id,
                "message_url": str(existing.message_url)
                if existing.message_url is not None
                else None,
            }
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
                "message_url": str(existing.message_url)
                if existing.message_url is not None
                else None,
            }
        repository.enqueue_delivery_once(delivery)
        sent = OutboxWorker(
            repository,
            transport_factory(guild_id=guild),
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
            "message_url": str(persisted.message_url)
            if persisted.message_url is not None
            else None,
        }
    finally:
        connection.close()


__all__ = [
    "DEFAULT_DISCORD_GUILD_ID",
    "DEFAULT_DISCORD_REVIEW_CHANNEL_ID",
    "legacy_artifact_root",
    "legacy_created_at",
    "run_legacy_review",
]
