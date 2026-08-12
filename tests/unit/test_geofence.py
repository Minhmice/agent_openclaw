import math
from dataclasses import replace

import pytest

from openclaw_web.geofence.distance import haversine_km
from openclaw_web.geofence.service import (
    AdministrativeFallbackAlias,
    AdministrativeFallbackDataset,
    AdministrativeFallbackEntry,
    Confidence,
    GeocoderResult,
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
    assert result.provider_reason == "geocoder_failed"
    assert "secret" not in result.reason


def test_versioned_district_fallback_normalizes_vietnamese_text() -> None:
    result = GeofenceService(21.0285, 105.8542, 80).evaluate(
        LocationEvidence(province=" hà nội ", district="  Hoàn   Kiếm ")
    )
    assert result.inside is True
    assert result.confidence is Confidence.MEDIUM
    assert result.method == "administrative-fallback"
    assert result.version == "hanoi-80km-fallback-v2"


def test_broad_or_missing_evidence_is_unresolved_never_assumed_inside() -> None:
    service = GeofenceService(21.0285, 105.8542, 80)
    broad = service.evaluate(LocationEvidence(province="Hải Phòng"))
    missing = service.evaluate(LocationEvidence())
    assert broad.inside is None
    assert broad.reason == "fallback_unknown"
    assert missing.inside is None
    assert missing.confidence is Confidence.LOW
    assert missing.distance_km is None


def test_fallback_dataset_is_versioned_immutable_and_bound_to_market() -> None:
    dataset = AdministrativeFallbackDataset.hanoi_80km()
    assert dataset.market_id == "hanoi-80km"
    assert dataset.version == "hanoi-80km-fallback-v2"
    assert dataset.effective_date == "2025-07-01"
    assert dataset.admin_system_version
    assert dataset.source_label
    assert dataset.content_hash
    with pytest.raises(AttributeError):
        dataset.entries += (  # type: ignore[misc]
            AdministrativeFallbackEntry("ha noi", "fake", True, Confidence.MEDIUM),
        )
    copied = AdministrativeFallbackDataset(
        market_id=dataset.market_id,
        center_latitude=dataset.center_latitude,
        center_longitude=dataset.center_longitude,
        radius_km=dataset.radius_km,
        version=dataset.version,
        effective_date=dataset.effective_date,
        admin_system_version=dataset.admin_system_version,
        source_label=dataset.source_label,
        entries=list(dataset.entries),  # type: ignore[arg-type]
        aliases=list(dataset.aliases),  # type: ignore[arg-type]
    )
    assert isinstance(copied.entries, tuple)
    assert isinstance(copied.aliases, tuple)

    mismatch = GeofenceService(0, 0, 1, fallback_dataset=dataset).evaluate(
        LocationEvidence(province="Hà Nội", district="Hoàn Kiếm")
    )
    assert mismatch.inside is None
    assert mismatch.confidence is Confidence.LOW
    assert mismatch.reason == "fallback_market_mismatch"


def test_fallback_content_hash_covers_all_semantics_and_ignores_collection_order() -> None:
    dataset = AdministrativeFallbackDataset.hanoi_80km()
    assert len(dataset.content_hash) == 64
    assert dataset.content_hash == dataset.content_hash.lower()
    assert (
        replace(dataset, entries=tuple(reversed(dataset.entries))).content_hash
        == dataset.content_hash
    )
    assert (
        replace(dataset, aliases=tuple(reversed(dataset.aliases))).content_hash
        == dataset.content_hash
    )

    assert (
        replace(dataset, center_latitude=dataset.center_latitude + 0.01).content_hash
        != dataset.content_hash
    )
    assert (
        replace(dataset, source_label=dataset.source_label + " amended").content_hash
        != dataset.content_hash
    )
    changed_entry = replace(dataset.entries[0], admin_code="01")
    assert (
        replace(dataset, entries=(changed_entry, *dataset.entries[1:])).content_hash
        != dataset.content_hash
    )
    changed_alias = replace(dataset.aliases[0], alias="ha noi municipality")
    assert (
        replace(dataset, aliases=(changed_alias, *dataset.aliases[1:])).content_hash
        != dataset.content_hash
    )

    with pytest.raises(TypeError):
        AdministrativeFallbackDataset(  # type: ignore[call-arg]
            market_id="spoof",
            center_latitude=0,
            center_longitude=0,
            radius_km=1,
            version="v1",
            effective_date="2025-01-01",
            admin_system_version="admin-v1",
            source_label="source",
            entries=(),
            content_hash="0" * 64,
        )


def test_western_quang_ninh_coordinate_wins_but_province_only_is_unknown() -> None:
    service = GeofenceService(21.0285, 105.8542, 80)
    coordinate = service.evaluate(LocationEvidence(latitude=21.106, longitude=106.49))
    province = service.evaluate(LocationEvidence(province="Quảng Ninh"))
    assert coordinate.inside is True
    assert coordinate.confidence is Confidence.HIGH
    assert province.inside is None
    assert province.reason == "fallback_unknown"


@pytest.mark.parametrize(
    ("province", "district"),
    (
        ("Thành phố Hà Nội", "Quận Hoàn Kiếm"),
        ("TP. Hà Nội", "Q. Hoàn Kiếm"),
        ("TP Hà Nội", "Q Hoàn Kiếm"),
    ),
)
def test_administrative_prefix_aliases_resolve_known_district(province: str, district: str) -> None:
    result = GeofenceService(21.0285, 105.8542, 80).evaluate(
        LocationEvidence(province=province, district=district)
    )
    assert result.inside is True
    assert result.reason == "fallback_district_inside"


def test_unknown_current_administrative_unit_is_unresolved() -> None:
    result = GeofenceService(21.0285, 105.8542, 80).evaluate(
        LocationEvidence(province="TP Hà Nội", district="Phường Không Có Thật")
    )
    assert result.inside is None
    assert result.reason == "fallback_unknown"


@pytest.mark.parametrize("unit", ("Phuong Ba Dinh", "Xa Ba Dinh"))
def test_current_unit_level_never_inherits_bare_legacy_district(unit: str) -> None:
    result = GeofenceService(21.0285, 105.8542, 80).evaluate(
        LocationEvidence(province="TP Ha Noi", district=unit)
    )
    assert result.inside is None
    assert result.reason == "fallback_unknown"


@pytest.mark.parametrize("unit", ("Ba Dinh", "Quan Ba Dinh", "Q Ba Dinh", "Huyen Ba Dinh"))
def test_bare_quan_and_huyen_legacy_district_compatibility(unit: str) -> None:
    result = GeofenceService(21.0285, 105.8542, 80).evaluate(
        LocationEvidence(province="TP Ha Noi", district=unit)
    )
    assert result.inside is True
    assert result.reason == "fallback_district_inside"


def test_explicit_current_unit_alias_resolves_only_when_dataset_defines_target() -> None:
    base = AdministrativeFallbackDataset.hanoi_80km()
    dataset = replace(
        base,
        aliases=(
            *base.aliases,
            AdministrativeFallbackAlias("phuong", "phuong ba dinh", "ba dinh"),
        ),
    )
    result = GeofenceService(21.0285, 105.8542, 80, fallback_dataset=dataset).evaluate(
        LocationEvidence(province="TP Ha Noi", district="Phuong Ba Dinh")
    )
    assert result.inside is True


def test_fallback_aliases_reject_dangling_and_ambiguous_targets() -> None:
    base = AdministrativeFallbackDataset.hanoi_80km()
    with pytest.raises(ValueError, match="alias target"):
        replace(
            base,
            aliases=(AdministrativeFallbackAlias("phuong", "phuong moi", "missing"),),
        )
    with pytest.raises(ValueError, match="alias collision"):
        replace(
            base,
            aliases=(
                AdministrativeFallbackAlias("phuong", "phuong moi", "ba dinh"),
                AdministrativeFallbackAlias("phuong", "phuong moi", "hoan kiem"),
            ),
        )
    with pytest.raises(ValueError, match="alias collision"):
        replace(
            base,
            aliases=(AdministrativeFallbackAlias("quan", "quan ba dinh", "hoan kiem"),),
        )


@pytest.mark.parametrize(
    ("geocoder", "expected_reason"),
    (
        (None, "geocoder_unconfigured"),
        (lambda _address: None, "geocoder_no_match"),
        (lambda _address: (999, 999), "geocoder_invalid_result"),
        (lambda _address: (21.0,), "geocoder_invalid_result"),
        (lambda _address: (None, 105.0), "geocoder_invalid_result"),
    ),
)
def test_geocoder_failure_reasons_are_exact_without_fallback(
    geocoder: object, expected_reason: str
) -> None:
    result = GeofenceService(21.0285, 105.8542, 80, geocoder=geocoder).evaluate(  # type: ignore[arg-type]
        LocationEvidence(address="Địa chỉ không xác định")
    )
    assert result.inside is None
    assert result.reason == expected_reason
    assert result.method == "unresolved"


def test_tuple_geocoder_is_medium_confidence_for_backwards_compatibility() -> None:
    result = GeofenceService(
        21.0285,
        105.8542,
        80,
        geocoder=lambda _address: (21.03, 105.85),
    ).evaluate(LocationEvidence(address="Hà Nội"))
    assert result.inside is True
    assert result.confidence is Confidence.MEDIUM
    assert result.geocoder_source == "legacy-tuple"


def test_structured_geocoder_preserves_provenance_and_boundary_uncertainty() -> None:
    radius = haversine_km(0, 0, 0, 1)
    result = GeofenceService(
        0,
        0,
        radius,
        geocoder=lambda _address: GeocoderResult(
            latitude=0,
            longitude=1,
            precision="street",
            source="approved-provider",
            accuracy_km=0.5,
        ),
        fallback_dataset=None,
    ).evaluate(LocationEvidence(address="near boundary"))
    assert result.inside is None
    assert result.confidence is Confidence.LOW
    assert result.reason == "geocoded_boundary_uncertain"
    assert result.geocoder_source == "approved-provider"
    assert result.geocoder_precision == "street"


def test_structured_geocoder_without_uncertainty_is_medium_confidence() -> None:
    result = GeofenceService(
        21.0285,
        105.8542,
        80,
        geocoder=lambda _address: GeocoderResult(
            latitude=21.0285,
            longitude=105.8542,
            precision="rooftop",
            source="approved-provider",
        ),
    ).evaluate(LocationEvidence(address="Hà Nội"))
    assert result.inside is True
    assert result.confidence is Confidence.MEDIUM
