"""Deterministic market geofencing primitives."""

from openclaw_web.geofence.distance import haversine_km
from openclaw_web.geofence.service import GeofenceResult, GeofenceService, LocationEvidence

__all__ = ["GeofenceResult", "GeofenceService", "LocationEvidence", "haversine_km"]
