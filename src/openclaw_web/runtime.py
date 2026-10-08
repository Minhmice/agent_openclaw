"""Compatibility façade for the OpenClaw runtime capability boundaries."""

from __future__ import annotations

from pathlib import Path

from openclaw_web.audit.lighthouse import LighthouseRunner
from openclaw_web.crawl import WebsiteCrawler, normalize_url, resolve_and_validate
from openclaw_web.db import Repository, connect, migrate
from openclaw_web.db.repository import ReviewProjectRecord
from openclaw_web.delivery.components import (
    DEFAULT_REVIEW_REJECTION_REASON,
    REVIEW_CARD_VERSION,
    ActionResult,
    ComponentActionEnvelope,
    ComponentActionService,
    ComponentSetRecord,
    build_review_card,
    component_record,
    parse_component_action_json,
)
from openclaw_web.delivery.openclaw_transport import OpenClawAgentTransport
from openclaw_web.delivery.outbox import OutboxWorker
from openclaw_web.delivery.workflow_adapter import WorkflowCoordinatorAdapter
from openclaw_web.discovery.base import AutomaticDiscoveryProvider, DiscoveryError
from openclaw_web.discovery.nominatim import NominatimDiscoverySource
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

from .runtime_adapters import (
    MutableReviewProject as _MutableReviewProject,
)
from .runtime_adapters import (
    RepositoryComponents as _RepositoryComponents,
)
from .runtime_adapters import (
    RepositoryProduction as _RepositoryProduction,
)
from .runtime_adapters import (
    SqliteRunLocks as _SqliteRunLocks,
)
from .runtime_components import (
    CoordinatorActions as _CoordinatorActions,
)
from .runtime_components import (
    bounded_project_value as _bounded_project_value,
)
from .runtime_components import (
    canonical_callback_envelope as _canonical_callback_envelope,
)
from .runtime_components import (
    component_gate_ready as _component_gate_ready,
)
from .runtime_components import (
    component_read_only as _component_read_only,
)
from .runtime_components import (
    component_result_message as _component_result_message,
)
from .runtime_components import (
    has_unresolved_priority as _has_unresolved_priority,
)
from .runtime_components import (
    project_snapshot_text as _project_snapshot_text,
)
from .runtime_components import (
    run_component_action,
    run_component_callback,
)
from .runtime_components import (
    strict_callback_json as _strict_callback_json,
)
from .runtime_components import (
    workflow_project as _workflow_project,
)
from .runtime_discovery import (
    CompositionReadiness,
    DiscoveryComposition,
    ProductionDiscoveryComposition,
    _configured_path,
    _resolve,
    drain_delivery_outbox,
    run_daily_discovery,
)
from .runtime_legacy import (
    _legacy_artifact_root,
    _legacy_created_at,
)
from .runtime_legacy import (
    run_legacy_review as _run_legacy_review_impl,
)
from .runtime_support import (
    required_discord_guild_id,
)
from .runtime_support import (
    state_db as _default_state_db,
)
from .runtime_support import (
    workflow_root as _default_workflow_root,
)

DEFAULT_DISCORD_GUILD_ID = "1446612692910739637"
DEFAULT_DISCORD_REVIEW_CHANNEL_ID = "1536658476288450630"


def _state_db() -> Path:
    return _default_state_db()


def _discord_guild_id() -> str:
    return required_discord_guild_id()


def _workflow_root() -> Path:
    return _default_workflow_root()


def run_legacy_review(
    project_id: str,
    *,
    workflow_root: Path | None = None,
    review_channel: str | None = None,
    guild_id: str | None = None,
    artifact_root: Path | None = None,
) -> dict[str, object]:
    """Keep the historical runtime import and monkeypatch seams stable."""

    root = workflow_root if workflow_root is not None else _workflow_root()
    artifacts = artifact_root if artifact_root is not None else _legacy_artifact_root(None)
    return _run_legacy_review_impl(
        project_id,
        workflow_root=root,
        review_channel=review_channel,
        guild_id=guild_id,
        artifact_root=artifacts,
        state_db_path=_state_db(),
        transport_factory=OpenClawAgentTransport,
    )


__all__ = [
    "DEFAULT_DISCORD_GUILD_ID",
    "DEFAULT_DISCORD_REVIEW_CHANNEL_ID",
    "DEFAULT_REVIEW_REJECTION_REASON",
    "REVIEW_CARD_VERSION",
    "ActionResult",
    "AutomaticDiscoveryProvider",
    "ComponentActionEnvelope",
    "ComponentActionService",
    "ComponentSetRecord",
    "CompositionReadiness",
    "CronResult",
    "CronRunner",
    "DeliveryRecord",
    "DeliveryState",
    "DiscoveryComposition",
    "DiscoveryError",
    "GooglePlacesDiscoverySource",
    "LegacyReviewError",
    "LighthouseRunner",
    "MarketConfig",
    "NominatimDiscoverySource",
    "OpenClawAgentTransport",
    "OutboxWorker",
    "OverpassDiscoverySource",
    "ProductionDiscoveryComposition",
    "ProductionPipeline",
    "ProductionProject",
    "ProjectState",
    "Repository",
    "ReviewProjectRecord",
    "Rubric",
    "ScreenshotRunner",
    "SerperDiscoverySource",
    "WebsiteCrawler",
    "WorkflowCoordinatorAdapter",
    "_CoordinatorActions",
    "_MutableReviewProject",
    "_RepositoryComponents",
    "_RepositoryProduction",
    "_SqliteRunLocks",
    "_bounded_project_value",
    "_canonical_callback_envelope",
    "_component_gate_ready",
    "_component_read_only",
    "_component_result_message",
    "_configured_path",
    "_discord_guild_id",
    "_has_unresolved_priority",
    "_legacy_artifact_root",
    "_legacy_created_at",
    "_project_snapshot_text",
    "_resolve",
    "_state_db",
    "_strict_callback_json",
    "_workflow_project",
    "_workflow_root",
    "build_review_card",
    "component_record",
    "connect",
    "drain_delivery_outbox",
    "load_legacy_review_project",
    "load_market",
    "load_rubric",
    "migrate",
    "normalize_url",
    "parse_component_action_json",
    "render_legacy_review_message",
    "resolve_and_validate",
    "run_component_action",
    "run_component_callback",
    "run_daily_discovery",
    "run_legacy_review",
]
