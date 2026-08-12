"""Evidence-aware geofence evaluation for the central Hanoi market."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol, TypeAlias

from openclaw_web.geofence.distance import haversine_km
from openclaw_web.models import Confidence

GEOFENCE_METHOD_VERSION = "haversine-iugg-v1"
# 1e-9 km is one micrometre. It only absorbs floating-point boundary noise and
# is deliberately far smaller than the accuracy of any supported location source.
BOUNDARY_TOLERANCE_KM = 1e-9
FALLBACK_CONFIG_TOLERANCE = 1e-6


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


_ADMIN_PREFIXES: tuple[tuple[str, str], ...] = (
    ("thanh pho", "province"),
    ("thi xa", "district"),
    ("phuong", "phuong"),
    ("huyen", "huyen"),
    ("quan", "quan"),
    ("xa", "xa"),
    ("tp", "province"),
    ("tx", "district"),
    ("p", "phuong"),
    ("h", "huyen"),
    ("q", "quan"),
    ("x", "xa"),
)
_ADMIN_LEVELS = frozenset({"province", "district", "bare", "quan", "huyen", "phuong", "xa"})


def _parse_admin_unit_detail(value: str, *, default_level: str) -> tuple[str, str, bool]:
    normalized = _normalize_public_text(value, field=default_level)
    assert normalized is not None
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    for prefix, level in _ADMIN_PREFIXES:
        if normalized.startswith(f"{prefix} "):
            return level, normalized[len(prefix) + 1 :], True
    return default_level, normalized, False


def _parse_admin_unit(value: str, *, default_level: str) -> tuple[str, str]:
    level, name, _ = _parse_admin_unit_detail(value, default_level=default_level)
    return level, name


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


def _nonnegative_finite(value: float | None, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} must be a finite nonnegative real number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
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
        for field_name in ("address", "province", "district"):
            object.__setattr__(
                self,
                field_name,
                _normalize_public_text(getattr(self, field_name), field=field_name),
            )
        for field_name in ("source", "provenance"):
            object.__setattr__(
                self,
                field_name,
                _preserve_identifier(getattr(self, field_name), field=field_name),
            )


@dataclass(frozen=True, slots=True)
class GeocoderResult:
    """Structured, attributable coordinates returned by an approved geocoder."""

    latitude: float
    longitude: float
    precision: str
    source: str
    accuracy_km: float | None = None

    def __post_init__(self) -> None:
        latitude = _coordinate(self.latitude, field="latitude", lower=-90, upper=90)
        longitude = _coordinate(self.longitude, field="longitude", lower=-180, upper=180)
        assert latitude is not None and longitude is not None
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(
            self, "precision", _preserve_identifier(self.precision, field="precision")
        )
        object.__setattr__(self, "source", _preserve_identifier(self.source, field="source"))
        object.__setattr__(
            self,
            "accuracy_km",
            _nonnegative_finite(self.accuracy_km, field="accuracy_km"),
        )


@dataclass(frozen=True, slots=True)
class AdministrativeFallbackEntry:
    """One explicit administrative decision in a version-bound fallback dataset."""

    province: str
    district: str | None
    inside: bool
    confidence: Confidence
    admin_code: str | None = None
    compatibility_alias: bool = False
    level: str | None = None

    def __post_init__(self) -> None:
        province_level, province = _parse_admin_unit(self.province, default_level="province")
        if province_level != "province":
            raise ValueError("fallback entry province must be province-level")
        district = None
        inferred_level = "province"
        if self.district is not None:
            inferred_level, district, explicit_prefix = _parse_admin_unit_detail(
                self.district, default_level="bare"
            )
        else:
            explicit_prefix = False
        level = self.level or inferred_level
        if district is not None and not explicit_prefix and level == "bare":
            raise ValueError(
                "bare legacy fallback entry requires explicit district compatibility mapping"
            )
        if level not in _ADMIN_LEVELS - {"bare"} or level == "province" and district is not None:
            raise ValueError("invalid fallback entry level")
        if district is None and level != "province":
            raise ValueError("province fallback entry must use province level")
        if district is not None:
            if explicit_prefix and level != inferred_level:
                raise ValueError("fallback entry prefix must match its level")
            if not explicit_prefix and not (level == "district" and self.compatibility_alias):
                raise ValueError(
                    "bare legacy fallback entry requires explicit district compatibility mapping"
                )
        if self.compatibility_alias and level != "district":
            raise ValueError("fallback entry compatibility alias must use district level")
        if not isinstance(self.inside, bool):
            raise TypeError("fallback entry inside must be boolean")
        if not isinstance(self.confidence, Confidence):
            raise TypeError("fallback entry confidence must be Confidence")
        if not isinstance(self.compatibility_alias, bool):
            raise TypeError("fallback entry compatibility_alias must be boolean")
        object.__setattr__(self, "province", province)
        object.__setattr__(self, "district", district)
        object.__setattr__(
            self, "admin_code", _preserve_identifier(self.admin_code, field="admin_code")
        )
        object.__setattr__(self, "level", level)


@dataclass(frozen=True, slots=True)
class AdministrativeFallbackAlias:
    """Effective-dated administrative naming metadata; not a spatial decision itself."""

    level: str
    alias: str
    canonical_name: str
    admin_code: str | None = None

    def __post_init__(self) -> None:
        if self.level not in _ADMIN_LEVELS - {"bare"}:
            raise ValueError("invalid fallback alias level")
        alias_level, alias = _parse_admin_unit(self.alias, default_level=self.level)
        if alias_level != self.level:
            raise ValueError("fallback alias prefix must match its level")
        canonical_level, canonical_name, canonical_prefix = _parse_admin_unit_detail(
            self.canonical_name, default_level=self.level
        )
        if canonical_prefix and canonical_level != self.level:
            raise ValueError("fallback alias canonical name prefix must match its level")
        object.__setattr__(self, "alias", alias)
        object.__setattr__(self, "canonical_name", canonical_name)
        object.__setattr__(
            self, "admin_code", _preserve_identifier(self.admin_code, field="admin_code")
        )


@dataclass(frozen=True, slots=True)
class AdministrativeFallbackDataset:
    """Immutable fallback facts tied to exact market geometry and admin metadata."""

    market_id: str
    center_latitude: float
    center_longitude: float
    radius_km: float
    version: str
    effective_date: str
    admin_system_version: str
    source_label: str
    entries: tuple[AdministrativeFallbackEntry, ...]
    aliases: tuple[AdministrativeFallbackAlias, ...] = ()
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        latitude = _coordinate(
            self.center_latitude,
            field="fallback center_latitude",
            lower=-90,
            upper=90,
        )
        longitude = _coordinate(
            self.center_longitude,
            field="fallback center_longitude",
            lower=-180,
            upper=180,
        )
        radius = _nonnegative_finite(self.radius_km, field="fallback radius_km")
        assert latitude is not None and longitude is not None and radius is not None
        object.__setattr__(self, "center_latitude", 0.0 if latitude == 0 else latitude)
        object.__setattr__(self, "center_longitude", 0.0 if longitude == 0 else longitude)
        object.__setattr__(self, "radius_km", 0.0 if radius == 0 else radius)
        for metadata_field in (
            "market_id",
            "version",
            "effective_date",
            "admin_system_version",
            "source_label",
        ):
            object.__setattr__(
                self,
                metadata_field,
                _preserve_identifier(getattr(self, metadata_field), field=metadata_field),
            )
        entries = tuple(self.entries)
        aliases = tuple(self.aliases)
        if not all(isinstance(entry, AdministrativeFallbackEntry) for entry in entries):
            raise TypeError("fallback entries must be AdministrativeFallbackEntry values")
        if not all(isinstance(alias, AdministrativeFallbackAlias) for alias in aliases):
            raise TypeError("fallback aliases must be AdministrativeFallbackAlias values")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "aliases", aliases)
        if len({self._entry_key(entry) for entry in entries}) != len(entries):
            raise ValueError("fallback entries must have unique stable keys")
        self._validate_aliases()
        object.__setattr__(self, "content_hash", self._derive_content_hash())

    @staticmethod
    def _entry_key(entry: AdministrativeFallbackEntry) -> tuple[str, str, str | None]:
        assert entry.level is not None
        return entry.level, entry.province, entry.district

    def _alias_target(self, alias: AdministrativeFallbackAlias) -> tuple[str, str, str | None]:
        candidates = tuple(
            entry
            for entry in self.entries
            if entry.level == alias.level
            and (
                entry.province == alias.canonical_name
                if alias.level == "province"
                else entry.district == alias.canonical_name
            )
            and (alias.admin_code is None or entry.admin_code == alias.admin_code)
        )
        if len(candidates) != 1:
            raise ValueError("fallback alias target must identify exactly one entry")
        return self._entry_key(candidates[0])

    def _validate_aliases(self) -> None:
        targets: dict[tuple[str, str], tuple[str, str, str | None]] = {}
        entry_names = {
            (level, entry.district)
            for entry in self.entries
            for level in (
                {entry.level}
                if not entry.compatibility_alias
                else {entry.level, "bare", "quan", "huyen"}
            )
            if entry.district is not None
        }
        for alias in self.aliases:
            identity = alias.level, alias.alias
            target = self._alias_target(alias)
            if identity in targets or identity in entry_names:
                raise ValueError("fallback alias collision")
            targets[identity] = target

    def _derive_content_hash(self) -> str:
        entries = sorted(
            (
                {
                    "level": entry.level,
                    "province": entry.province,
                    "district": entry.district,
                    "admin_code": entry.admin_code,
                    "inside": entry.inside,
                    "confidence": entry.confidence.value,
                    "compatibility_alias": entry.compatibility_alias,
                }
                for entry in self.entries
            ),
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
        aliases = sorted(
            (
                {
                    "level": alias.level,
                    "alias": alias.alias,
                    "canonical_name": alias.canonical_name,
                    "admin_code": alias.admin_code,
                }
                for alias in self.aliases
            ),
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
        material = {
            "market_id": self.market_id,
            "center_latitude": self.center_latitude,
            "center_longitude": self.center_longitude,
            "radius_km": self.radius_km,
            "version": self.version,
            "effective_date": self.effective_date,
            "admin_system_version": self.admin_system_version,
            "source_label": self.source_label,
            "entries": entries,
            "aliases": aliases,
        }
        encoded = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def hanoi_80km(cls) -> AdministrativeFallbackDataset:
        districts = (
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
        entries = (
            AdministrativeFallbackEntry("ha noi", None, True, Confidence.MEDIUM),
            *(
                AdministrativeFallbackEntry(
                    "ha noi",
                    district,
                    True,
                    Confidence.MEDIUM,
                    compatibility_alias=True,
                    level="district",
                )
                for district in districts
            ),
        )
        aliases = (AdministrativeFallbackAlias("province", "hanoi municipality", "ha noi"),)
        return cls(
            market_id="hanoi-80km",
            center_latitude=21.0285,
            center_longitude=105.8542,
            radius_km=80.0,
            version="hanoi-80km-fallback-v2",
            effective_date="2025-07-01",
            admin_system_version=(
                "Vietnam two-tier administration effective 2025-07-01; "
                "pre-2025 Hanoi districts retained only as compatibility aliases"
            ),
            source_label="curated Hanoi 80 km administrative fallback",
            entries=entries,
            aliases=aliases,
        )


@dataclass(frozen=True, slots=True)
class GeofenceResult:
    """Tri-state geofence decision with auditable method and provider provenance."""

    inside: bool | None
    distance_km: float | None
    confidence: Confidence
    reason: str
    method: str
    version: str
    provider_reason: str | None = None
    geocoder_source: str | None = None
    geocoder_precision: str | None = None
    geocoder_accuracy_km: float | None = None
    fallback_admin_system_version: str | None = None


GeocoderValue: TypeAlias = GeocoderResult | tuple[float, float]


class Geocoder(Protocol):
    """Injected deterministic geocoder; implementations own any external I/O."""

    def __call__(self, normalized_public_address: str) -> GeocoderValue | None: ...


_DEFAULT_DATASET = object()


class GeofenceService:
    """Evaluate coordinates, then geocoding, then conservative administrative data."""

    def __init__(
        self,
        center_latitude: float,
        center_longitude: float,
        radius_km: float,
        *,
        geocoder: Geocoder | None = None,
        fallback_dataset: AdministrativeFallbackDataset | None | object = _DEFAULT_DATASET,
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
        radius = _nonnegative_finite(radius_km, field="radius_km")
        assert radius is not None
        self._radius_km = radius
        self._geocoder = geocoder
        self._fallback_dataset = (
            AdministrativeFallbackDataset.hanoi_80km()
            if fallback_dataset is _DEFAULT_DATASET
            else fallback_dataset
        )

    def _coordinate_result(
        self,
        latitude: float,
        longitude: float,
        *,
        prefix: str,
        confidence: Confidence,
        geocoder_result: GeocoderResult | None = None,
    ) -> GeofenceResult:
        distance = haversine_km(
            self._center_latitude,
            self._center_longitude,
            latitude,
            longitude,
        )
        accuracy = geocoder_result.accuracy_km if geocoder_result is not None else None
        if (
            accuracy is not None
            and accuracy > 0
            and (
                distance - accuracy <= self._radius_km + BOUNDARY_TOLERANCE_KM
                and distance + accuracy >= self._radius_km - BOUNDARY_TOLERANCE_KM
            )
        ):
            inside: bool | None = None
            reason = "geocoded_boundary_uncertain"
            confidence = Confidence.LOW
        else:
            inside = distance <= self._radius_km + BOUNDARY_TOLERANCE_KM
            reason = f"{prefix}_{'within' if inside else 'outside'}_radius"
        return GeofenceResult(
            inside=inside,
            distance_km=distance,
            confidence=confidence,
            reason=reason,
            method="coordinates" if prefix == "coordinates" else "geocoder",
            version=GEOFENCE_METHOD_VERSION,
            geocoder_source=geocoder_result.source if geocoder_result else None,
            geocoder_precision=geocoder_result.precision if geocoder_result else None,
            geocoder_accuracy_km=accuracy,
        )

    def _dataset_matches_market(self, dataset: AdministrativeFallbackDataset) -> bool:
        return (
            abs(self._center_latitude - dataset.center_latitude) <= FALLBACK_CONFIG_TOLERANCE
            and abs(self._center_longitude - dataset.center_longitude) <= FALLBACK_CONFIG_TOLERANCE
            and abs(self._radius_km - dataset.radius_km) <= FALLBACK_CONFIG_TOLERANCE
        )

    def _administrative_result(
        self,
        evidence: LocationEvidence,
        *,
        provider_reason: str | None,
    ) -> GeofenceResult:
        dataset = self._fallback_dataset
        if dataset is None:
            return GeofenceResult(
                None,
                None,
                Confidence.LOW,
                provider_reason or "fallback_unconfigured",
                "unresolved",
                GEOFENCE_METHOD_VERSION,
                provider_reason=provider_reason,
            )
        assert isinstance(dataset, AdministrativeFallbackDataset)
        if not self._dataset_matches_market(dataset):
            return GeofenceResult(
                None,
                None,
                Confidence.LOW,
                "fallback_market_mismatch",
                "unresolved",
                dataset.version,
                provider_reason=provider_reason,
                fallback_admin_system_version=dataset.admin_system_version,
            )
        if evidence.province is None:
            return GeofenceResult(
                None,
                None,
                Confidence.LOW,
                provider_reason or "location_evidence_missing",
                "unresolved",
                dataset.version,
                provider_reason=provider_reason,
                fallback_admin_system_version=dataset.admin_system_version,
            )
        province_level, province = _parse_admin_unit(evidence.province, default_level="province")
        if province_level != "province":
            province = ""
        province_alias = next(
            (
                alias
                for alias in dataset.aliases
                if alias.level == "province" and alias.alias == province
            ),
            None,
        )
        if province_alias is not None:
            province = province_alias.canonical_name
        source_level: str | None = None
        district: str | None = None
        if evidence.district is not None:
            source_level, district = _parse_admin_unit(evidence.district, default_level="bare")
        alias_target: tuple[str, str, str | None] | None = None
        if source_level is not None and district is not None:
            alias = next(
                (
                    candidate
                    for candidate in dataset.aliases
                    if candidate.level == source_level and candidate.alias == district
                ),
                None,
            )
            if alias is not None:
                alias_target = dataset._alias_target(alias)
        match = next(
            (
                entry
                for entry in dataset.entries
                if entry.province == province
                and (
                    (district is None and entry.district is None)
                    or (alias_target is not None and dataset._entry_key(entry) == alias_target)
                    or (
                        entry.district == district
                        and (
                            entry.level == source_level
                            or (
                                entry.compatibility_alias
                                and entry.level == "district"
                                and source_level in {"bare", "quan", "huyen"}
                            )
                        )
                    )
                )
            ),
            None,
        )
        if match is None and district is not None:
            # A named unknown unit must remain unresolved; do not widen it to a
            # province-only decision and accidentally bless stale/current names.
            reason = "fallback_unknown"
        elif match is None:
            reason = "fallback_unknown"
        else:
            granularity = "district" if match.district is not None else "province"
            reason = f"fallback_{granularity}_{'inside' if match.inside else 'outside'}"
        return GeofenceResult(
            inside=match.inside if match is not None else None,
            distance_km=None,
            confidence=match.confidence if match is not None else Confidence.LOW,
            reason=reason,
            method="administrative-fallback" if match is not None else "unresolved",
            version=dataset.version,
            provider_reason=provider_reason,
            fallback_admin_system_version=dataset.admin_system_version,
        )

    @staticmethod
    def _coerce_geocoder_result(value: object) -> tuple[GeocoderResult, Confidence]:
        if isinstance(value, GeocoderResult):
            return value, Confidence.MEDIUM
        if isinstance(value, tuple) and len(value) == 2:
            latitude = _coordinate(value[0], field="latitude", lower=-90, upper=90)
            longitude = _coordinate(value[1], field="longitude", lower=-180, upper=180)
            if latitude is None or longitude is None:
                raise ValueError("geocoder coordinates must not be null")
            return (
                GeocoderResult(latitude, longitude, "unspecified", "legacy-tuple"),
                Confidence.MEDIUM,
            )
        raise ValueError("malformed geocoder result")

    def evaluate(self, evidence: LocationEvidence) -> GeofenceResult:
        """Resolve evidence in authoritative order without converting unknown to inside."""

        if evidence.latitude is not None and evidence.longitude is not None:
            return self._coordinate_result(
                evidence.latitude,
                evidence.longitude,
                prefix="coordinates",
                confidence=Confidence.HIGH,
            )

        provider_reason: str | None = None
        if evidence.address is not None:
            if self._geocoder is None:
                provider_reason = "geocoder_unconfigured"
            else:
                try:
                    raw_geocoded = self._geocoder(evidence.address)
                except Exception:  # noqa: BLE001 - provider details may contain sensitive data
                    provider_reason = "geocoder_failed"
                else:
                    if raw_geocoded is None:
                        provider_reason = "geocoder_no_match"
                    else:
                        try:
                            geocoded, confidence = self._coerce_geocoder_result(raw_geocoded)
                        except (TypeError, ValueError):
                            provider_reason = "geocoder_invalid_result"
                        else:
                            return self._coordinate_result(
                                geocoded.latitude,
                                geocoded.longitude,
                                prefix="geocoded",
                                confidence=confidence,
                                geocoder_result=geocoded,
                            )
        return self._administrative_result(evidence, provider_reason=provider_reason)
