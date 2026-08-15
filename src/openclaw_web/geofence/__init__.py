"""Deterministic market geofencing primitives."""

from openclaw_web.geofence.distance import haversine_km
from openclaw_web.geofence.service import (
    AdministrativeFallbackAlias,
    AdministrativeFallbackDataset,
    AdministrativeFallbackEntry,
    GeocoderResult,
    GeofenceResult,
    GeofenceService,
    LocationEvidence,
)

__all__ = [
    "AdministrativeFallbackAlias",
    "AdministrativeFallbackDataset",
    "AdministrativeFallbackEntry",
    "GeocoderResult",
    "GeofenceResult",
    "GeofenceService",
    "LocationEvidence",
    "haversine_km",
]
