"""Scheduled discovery, delivery draining, and production composition boundary (backward compatibility shim)."""

from __future__ import annotations

from openclaw_web.discovery import (
    CompositionReadiness,
    DiscoveryComposition,
    ProductionDiscoveryComposition,
    configured_path,
    drain_delivery_outbox,
    run_daily_discovery,
)

__all__ = [
    "CompositionReadiness",
    "DiscoveryComposition",
    "ProductionDiscoveryComposition",
    "configured_path",
    "drain_delivery_outbox",
    "run_daily_discovery",
]
