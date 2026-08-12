"""Evidence-aware geofence evaluation for the central Hanoi market."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from openclaw_web.geofence.distance import haversine_km
from openclaw_web.models import Confidence

FALLBACK_VERSION = "hanoi-80km-fallback-v1"
GEOFENCE_METHOD_VERSION = "haversine-iugg-v1"
# Floating-point comparisons within one nanometre expressed in kilometres are
# treated as boundary equality. This is deterministic and far below source accuracy.
BOUNDARY_TOLERANCE_KM = 1e-9


def _normalize_public_text(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = normalized.replace("đ", "d").replace("Đ", "D").casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        raise ValueError(f"{field} must not be blank")
    return normalized


def _preserve_identifier(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    result = value.strip()
    if not result:
        raise ValueError(f"{field} must not be blank")
    return result


def _coordinate(value: float | None, *, field: str, lower: float, upper: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} must be a finite real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    if not lower <= result <= upper:
        raise ValueError(f"{field} is out of range")
    return result


@dataclass(frozen=True, slots=True)
class LocationEvidence:
    """Immutable normalized public location evidence; coordinates must be paired."""

    latitude: float | None = None
    longitude: float | None = None
    address: str | None = None
    province: str | None = None
    district: str | None = None
    source: str | None = None
    provenance: str | None = None

    def __post_init__(self) -> None:
        latitude = _coordinate(self.latitude, field="latitude", lower=-90, upper=90)
        longitude = _coordinate(self.longitude, field="longitude", lower=-180, upper=180)
        if (latitude is None) != (longitude is None):
            raise ValueError("latitude and longitude must be supplied together")
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)
        for field in ("address", "province", "district"):
            object.__setattr__(
                self,
                field,
                _normalize_public_text(getattr(self, field), field=field),
            )
        for field in ("source", "provenance"):
            object.__setattr__(
                self,
                field,
                _preserve_identifier(getattr(self, field), field=field),
            )


@dataclass(frozen=True, slots=True)
class GeofenceResult:
    """Tri-state geofence decision with an auditable method and reason."""

    inside: bool | None
    distance_km: float | None
    confidence: Confidence
    reason: str
    method: str
    version: str


class Geocoder(Protocol):
    """Injected deterministic geocoder; implementations own any external I/O."""

    def __call__(self, normalized_public_address: str) -> tuple[float, float] | None: ...


# Province-only Hanoi is useful but not coordinate-level proof, so it remains
# medium confidence. Broad neighbouring provinces are intentionally not marked in.
_PROVINCE_FALLBACK: dict[str, bool] = {
    "ha noi": True,
    "quang ninh": False,
    "thanh hoa": False,
}
_DISTRICT_FALLBACK: dict[tuple[str, str], bool] = {
    ("ha noi", district): True
    for district in (
        "ba dinh",
        "bac tu liem",
        "cau giay",
        "dong da",
        "ha dong",
        "hai ba trung",
        "hoan kiem",
        "hoang mai",
        "long bien",
        "nam tu liem",
        "tay ho",
        "thanh xuan",
    )
}


class GeofenceService:
    """Evaluate coordinates, then geocoding, then conservative administrative data."""

    def __init__(
        self,
        center_latitude: float,
        center_longitude: float,
        radius_km: float,
        *,
        geocoder: Geocoder | None = None,
    ) -> None:
        center_latitude_value = _coordinate(
            center_latitude, field="center_latitude", lower=-90, upper=90
        )
        center_longitude_value = _coordinate(
            center_longitude, field="center_longitude", lower=-180, upper=180
        )
        assert center_latitude_value is not None and center_longitude_value is not None
        self._center_latitude = center_latitude_value
        self._center_longitude = center_longitude_value
        if isinstance(radius_km, bool) or not isinstance(radius_km, int | float):
            raise TypeError("radius_km must be a finite nonnegative real number")
        self._radius_km = float(radius_km)
        if not math.isfinite(self._radius_km) or self._radius_km < 0:
            raise ValueError("radius_km must be finite and nonnegative")
        self._geocoder = geocoder

    def _from_coordinates(
        self, latitude: float, longitude: float, *, prefix: str
    ) -> GeofenceResult:
        distance = haversine_km(
            self._center_latitude,
            self._center_longitude,
            latitude,
            longitude,
        )
        inside = distance <= self._radius_km + BOUNDARY_TOLERANCE_KM
        return GeofenceResult(
            inside=inside,
            distance_km=distance,
            confidence=Confidence.HIGH,
            reason=f"{prefix}_{'within' if inside else 'outside'}_radius",
            method="coordinates" if prefix == "coordinates" else "geocoder",
            version=GEOFENCE_METHOD_VERSION,
        )

    def evaluate(self, evidence: LocationEvidence) -> GeofenceResult:
        """Resolve evidence in authoritative order without converting unknown to inside."""

        if evidence.latitude is not None and evidence.longitude is not None:
            return self._from_coordinates(
                evidence.latitude, evidence.longitude, prefix="coordinates"
            )
        if evidence.address is not None and self._geocoder is not None:
            try:
                geocoded = self._geocoder(evidence.address)
                if geocoded is not None:
                    latitude, longitude = geocoded
                    validated_latitude = _coordinate(
                        latitude, field="latitude", lower=-90, upper=90
                    )
                    validated_longitude = _coordinate(
                        longitude, field="longitude", lower=-180, upper=180
                    )
                    assert validated_latitude is not None and validated_longitude is not None
                    return self._from_coordinates(
                        validated_latitude, validated_longitude, prefix="geocoded"
                    )
            except Exception:  # noqa: BLE001 - external provider boundary must fail closed
                # Provider details can contain sensitive request data; never expose them.
                geocoded = None
        fallback = None
        reason = "fallback_unknown"
        if evidence.province is not None and evidence.district is not None:
            fallback = _DISTRICT_FALLBACK.get((evidence.province, evidence.district))
            if fallback is not None:
                reason = f"fallback_district_{'inside' if fallback else 'outside'}"
        if fallback is None and evidence.province is not None:
            fallback = _PROVINCE_FALLBACK.get(evidence.province)
            if fallback is not None:
                reason = f"fallback_province_{'inside' if fallback else 'outside'}"
        return GeofenceResult(
            inside=fallback,
            distance_km=None,
            confidence=Confidence.MEDIUM if fallback is not None else Confidence.LOW,
            reason=reason if evidence.province is not None else "location_evidence_missing",
            method="administrative-fallback" if evidence.province is not None else "unresolved",
            version=FALLBACK_VERSION,
        )
