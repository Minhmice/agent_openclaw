import math

import pytest

from openclaw_web.geofence.distance import haversine_km
from openclaw_web.geofence.service import (
    Confidence,
    GeofenceService,
    LocationEvidence,
)


def test_hanoi_center_is_inside_and_haiphong_is_outside_80km() -> None:
    service = GeofenceService(21.0285, 105.8542, 80)
    assert service.evaluate(LocationEvidence(latitude=21.0285, longitude=105.8542)).inside
    assert not service.evaluate(LocationEvidence(latitude=20.8449, longitude=106.6881)).inside


def test_haversine_is_symmetric_zero_and_handles_antimeridian_and_poles() -> None:
    assert haversine_km(21.0285, 105.8542, 21.0285, 105.8542) == 0.0
    forward = haversine_km(21.0285, 105.8542, 20.8449, 106.6881)
    assert forward == pytest.approx(haversine_km(20.8449, 106.6881, 21.0285, 105.8542))
    assert forward == pytest.approx(88.9, abs=0.5)
    assert haversine_km(0, 179.9, 0, -179.9) == pytest.approx(22.24, abs=0.05)
    assert haversine_km(90, 0, 90, 180) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("value", [True, False, math.nan, math.inf, -math.inf, "21"])
def test_haversine_rejects_non_finite_real_coordinates(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        haversine_km(value, 0, 0, 0)  # type: ignore[arg-type]


@pytest.mark.parametrize("args", [(91, 0, 0, 0), (0, 181, 0, 0)])
def test_haversine_rejects_out_of_range_coordinates(args: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        haversine_km(*args)


def test_boundary_is_inside_with_documented_tolerance() -> None:
    distance = haversine_km(0, 0, 0, 1)
    service = GeofenceService(0, 0, distance)
    result = service.evaluate(LocationEvidence(latitude=0, longitude=1))
    assert result.inside is True
    assert result.reason == "coordinates_within_radius"
    assert result.confidence is Confidence.HIGH


def test_location_rejects_partial_coordinates_and_is_immutable() -> None:
    with pytest.raises(ValueError, match="together"):
        LocationEvidence(latitude=21.0)
    evidence = LocationEvidence(province=" Hà   NỘI ")
    assert evidence.province == "ha noi"
    with pytest.raises(AttributeError):
        evidence.province = "other"  # type: ignore[misc]


def test_location_preserves_source_and_provenance_identifiers() -> None:
    evidence = LocationEvidence(
        address=" Hà Nội ",
        source=" Public Registry ",
        provenance="https://Example.test/CaseSensitive",
    )
    assert evidence.address == "ha noi"
    assert evidence.source == "Public Registry"
    assert evidence.provenance == "https://Example.test/CaseSensitive"


def test_coordinates_are_authoritative_and_skip_geocoder() -> None:
    calls: list[str] = []

    def geocoder(address: str) -> tuple[float, float]:
        calls.append(address)
        return (21.0285, 105.8542)

    service = GeofenceService(21.0285, 105.8542, 80, geocoder=geocoder)
    result = service.evaluate(
        LocationEvidence(
            latitude=20.8449,
            longitude=106.6881,
            address="Hà Nội",
            province="Hà Nội",
        )
    )
    assert result.inside is False
    assert calls == []


def test_geocoder_uses_normalized_public_address_before_allowlist() -> None:
    calls: list[str] = []

    def geocoder(address: str) -> tuple[float, float]:
        calls.append(address)
        return (20.8449, 106.6881)

    result = GeofenceService(21.0285, 105.8542, 80, geocoder=geocoder).evaluate(
        LocationEvidence(address="  1   Phố HUẾ, Hà Nội ", province="Hà Nội")
    )
    assert calls == ["1 pho hue, ha noi"]
    assert result.inside is False
    assert result.reason == "geocoded_outside_radius"


def test_geocoder_error_is_sanitized_and_falls_back() -> None:
    def geocoder(address: str) -> tuple[float, float]:
        raise RuntimeError("secret token must not escape")

    result = GeofenceService(21.0285, 105.8542, 80, geocoder=geocoder).evaluate(
        LocationEvidence(address="private", province="HÀ NỘI")
    )
    assert result.inside is True
    assert result.confidence is Confidence.MEDIUM
    assert result.reason == "fallback_province_inside"
    assert "secret" not in result.reason


def test_versioned_district_fallback_normalizes_vietnamese_text() -> None:
    result = GeofenceService(21.0285, 105.8542, 80).evaluate(
        LocationEvidence(province=" hà nội ", district="  Hoàn   Kiếm ")
    )
    assert result.inside is True
    assert result.confidence is Confidence.MEDIUM
    assert result.method == "administrative-fallback"
    assert result.version == "hanoi-80km-fallback-v1"


def test_broad_or_missing_evidence_is_unresolved_never_assumed_inside() -> None:
    service = GeofenceService(21.0285, 105.8542, 80)
    broad = service.evaluate(LocationEvidence(province="Hải Phòng"))
    missing = service.evaluate(LocationEvidence())
    assert broad.inside is None
    assert broad.reason == "fallback_unknown"
    assert missing.inside is None
    assert missing.confidence is Confidence.LOW
    assert missing.distance_km is None
