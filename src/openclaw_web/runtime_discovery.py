"""Scheduled discovery, delivery draining, and production composition boundary."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from openclaw_web.audit.lighthouse import LighthouseRunner
from openclaw_web.crawl import WebsiteCrawler, normalize_url, resolve_and_validate
from openclaw_web.db import Repository, connect, migrate
from openclaw_web.delivery.openclaw_transport import OpenClawAgentTransport
from openclaw_web.delivery.outbox import OutboxWorker
from openclaw_web.discovery.base import AutomaticDiscoveryProvider, DiscoveryError
from openclaw_web.discovery.nominatim import NominatimDiscoverySource
from openclaw_web.discovery.overpass import OverpassDiscoverySource
from openclaw_web.discovery.places import GooglePlacesDiscoverySource
from openclaw_web.discovery.serper import SerperDiscoverySource
from openclaw_web.models import DeliveryState
from openclaw_web.pipeline.cron import CronResult, CronRunner
from openclaw_web.pipeline.production import ProductionPipeline
from openclaw_web.scoring.rules import Rubric, load_rubric
from openclaw_web.screenshots import ScreenshotRunner
from openclaw_web.settings import MarketConfig, load_market

from .runtime_adapters import RepositoryProduction, SqliteRunLocks
from .runtime_support import required_discord_guild_id, state_db, workflow_root

DiscoveryComposition = Callable[[], str]
CompositionReadiness = Literal[
    "artifact_root_not_absolute",
    "artifact_root_unavailable",
    "market_config_unavailable",
    "scoring_config_unavailable",
    "rubric_config_unavailable",
    "review_channel_not_configured",
    "ready",
]


def configured_path(name: str, default: Path) -> Path:
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
        artifact_root = configured_path(
            "OPENCLAW_WEB_ARTIFACT_ROOT",
            Path.home() / ".local/share/openclaw-web/artifacts",
        )
        market_config = configured_path(
            "OPENCLAW_WEB_MARKET_CONFIG", root / "config/markets/hanoi-80km.yaml"
        )
        scoring_config = configured_path(
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
        providers: list[AutomaticDiscoveryProvider] = [
            OverpassDiscoverySource(),
            NominatimDiscoverySource(
                endpoint=os.environ.get(
                    "OPENCLAW_WEB_NOMINATIM_ENDPOINT",
                    "https://nominatim.openstreetmap.org/search",
                )
            ),
        ]
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
            return "rubric_config_unavailable"
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
        connection = connect(state_db())
        crawler = WebsiteCrawler()
        try:
            migrate(connection)
            repository = Repository(connection)
            durable = RepositoryProduction(repository, workflow_root())
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
                OpenClawAgentTransport(guild_id=required_discord_guild_id()),
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
    connection = connect(state_db())
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
            OpenClawAgentTransport(guild_id=required_discord_guild_id()),
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
    """Run the bounded daily boundary through an injected deterministic composition."""

    state_database = state_db()
    connection = connect(state_database)
    try:
        migrate(connection)
        schedule_date = datetime.now(UTC).date()
    finally:
        connection.close()
    return CronRunner(
        SqliteRunLocks(state_database),
        discover or ProductionDiscoveryComposition.from_environment(),
        renew_interval_seconds=renew_interval_seconds,
    ).run(schedule_date)


# Compatibility names for code that imported private helpers from runtime.
_configured_path = configured_path
