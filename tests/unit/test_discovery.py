from __future__ import annotations

import csv
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

import openclaw_web.discovery.batch as batch_module
from openclaw_web.db.repository import (
    DiscoverySeedDisposition,
    DiscoverySeedUpsertResult,
)
from openclaw_web.discovery import (
    CsvDiscoverySource,
    DiscoveryConfigurationError,
    DiscoveryPayloadError,
    DiscoveryService,
    JsonDiscoverySource,
    ManualUrlDiscoverySource,
)
from openclaw_web.geofence import GeofenceService
from openclaw_web.models import CandidateSeed

NOW = datetime(2026, 8, 12, 3, 4, 5, tzinfo=UTC)


def _clock() -> datetime:
    return NOW


def test_csv_source_preserves_vietnamese_and_service_collapses_www_fixture() -> None:
    path = Path(__file__).parents[1] / "fixtures" / "discovery" / "seeds.csv"

    raw = CsvDiscoverySource(path, clock=_clock).discover()
    unique = DiscoveryService().normalize_unique(raw)

    assert len(raw) == 2
    assert raw[0].business_name == "Công ty Hà Nội"
    assert len(unique) == 1
    assert str(unique[0].url) == "https://example.com/"
    assert str(unique[0].source_url) == "https://directory.example/evidence/1"


def test_manual_source_defaults_evidence_url_and_injected_utc_clock() -> None:
    source = ManualUrlDiscoverySource(
        [{"url": "https://example.vn", "business_name": "Nhà máy Việt"}], clock=_clock
    )

    seed = source.discover()[0]

    assert seed.source_type == "manual"
    assert seed.source_url == seed.url
    assert seed.discovered_at == NOW


def test_manual_source_rejects_overflow_after_bounded_generator_consumption() -> None:
    consumed = 0

    def records() -> Iterator[dict[str, str]]:
        nonlocal consumed
        for index in range(10_000):
            consumed += 1
            yield {"url": f"https://{index}.example", "business_name": str(index)}

    source = ManualUrlDiscoverySource(records(), max_items=2, clock=_clock)

    with pytest.raises(DiscoveryPayloadError, match="too many manual records"):
        source.discover()

    assert consumed == 3


@pytest.mark.parametrize("max_items", [True, 0, -1, 201])
def test_manual_source_max_items_is_a_strict_bounded_positive_integer(max_items: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ManualUrlDiscoverySource([], max_items=max_items)  # type: ignore[arg-type]


def test_manual_source_rejects_string_like_iterables() -> None:
    with pytest.raises(TypeError, match="non-string iterable"):
        ManualUrlDiscoverySource("https://example.com")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "record",
    [
        {"url": "https://example.com", "business_name": "X", "latitude": True, "longitude": 1.0},
        {"url": "https://example.com", "business_name": "X", "latitude": 1.0},
        {
            "url": "https://example.com",
            "business_name": "X",
            "discovered_at": "2026-01-01T00:00:00",
        },
        {"url": "https://example.com", "business_name": "X", "metadata": "not-json"},
        {"url": "https://example.com", "business_name": "X", "unknown": "x"},
    ],
)
def test_manual_source_rejects_malformed_records(record: dict[str, object]) -> None:
    with pytest.raises(DiscoveryPayloadError, match="invalid manual record"):
        ManualUrlDiscoverySource([record], clock=_clock).discover()


def test_csv_rejects_duplicate_headers_blank_rows_and_bounds(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text(
        "url,url,business_name\nhttps://x.test,https://x.test,X\n", encoding="utf-8"
    )
    blank = tmp_path / "blank.csv"
    blank.write_text("url,business_name\nhttps://x.test,X\n,\n", encoding="utf-8")
    large = tmp_path / "large.csv"
    large.write_text("url,business_name\nhttps://x.test,Example\n", encoding="utf-8")

    with pytest.raises(DiscoveryPayloadError, match="duplicate.csv"):
        CsvDiscoverySource(duplicate, clock=_clock).discover()
    with pytest.raises(DiscoveryPayloadError, match="blank.csv"):
        CsvDiscoverySource(blank, clock=_clock).discover()
    with pytest.raises(DiscoveryPayloadError, match="large.csv"):
        CsvDiscoverySource(large, clock=_clock, max_bytes=4).discover()


def test_csv_bom_policy_is_explicit_and_row_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_text(
        "url,business_name\nhttps://a.test,A\nhttps://b.test,B\n",
        encoding="utf-8-sig",
    )

    assert len(CsvDiscoverySource(path, clock=_clock, allow_utf8_bom=True).discover()) == 2
    with pytest.raises(DiscoveryPayloadError, match="bom.csv"):
        CsvDiscoverySource(path, clock=_clock, allow_utf8_bom=False).discover()
    with pytest.raises(DiscoveryPayloadError, match="bom.csv"):
        CsvDiscoverySource(path, clock=_clock, max_items=1).discover()


def test_file_sources_reject_directories_and_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.csv"
    target.write_text("url,business_name\nhttps://x.test,X\n", encoding="utf-8")
    link = tmp_path / "link.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    for path in (tmp_path, link):
        with pytest.raises(DiscoveryPayloadError, match="invalid discovery file"):
            CsvDiscoverySource(path, clock=_clock).discover()


def test_file_source_reads_only_max_bytes_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "large.json"
    with path.open("wb") as handle:
        handle.seek(1_000_000)
        handle.write(b"x")
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("unbounded read_bytes used"))
    with pytest.raises(DiscoveryPayloadError, match="large.json"):
        JsonDiscoverySource(path, max_bytes=16).discover()


def test_csv_field_size_limit_is_restored_across_concurrent_parses(tmp_path: Path) -> None:
    small = tmp_path / "small.csv"
    large = tmp_path / "large.csv"
    small.write_text("url,business_name\nhttps://x.test," + "x" * 32 + "\n", encoding="utf-8")
    large.write_text("url,business_name\nhttps://y.test," + "y" * 8 + "\n", encoding="utf-8")
    original = csv.field_size_limit()

    def parse(path: Path, size: int) -> str:
        try:
            return CsvDiscoverySource(path, max_field_size=size).discover()[0].business_name
        except DiscoveryPayloadError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(parse, small, 16)
        second = pool.submit(parse, large, 64)
    assert first.result() == "rejected"
    assert second.result() == "y" * 8
    assert csv.field_size_limit() == original


def test_csv_field_size_limit_lock_covers_header_and_row_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "seeds.csv"
    path.write_text("url,business_name\nhttps://x.test,X\n", encoding="utf-8")
    original_reader = csv.reader
    lock_states: list[bool] = []

    def guarded_reader(*args: object, **kwargs: object):
        lock_states.append(batch_module._CSV_LIMIT_LOCK.locked())
        return original_reader(*args, **kwargs)

    monkeypatch.setattr(csv, "reader", guarded_reader)
    CsvDiscoverySource(path).discover()

    assert lock_states and all(lock_states)


def test_json_rejects_duplicate_keys_at_any_depth_and_non_list_root(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '[{"url":"https://x.test","business_name":"X","metadata":{"a":1,"a":2}}]',
        encoding="utf-8",
    )
    root = tmp_path / "root.json"
    root.write_text('{"url":"https://x.test"}', encoding="utf-8")

    with pytest.raises(DiscoveryPayloadError, match="duplicate.json"):
        JsonDiscoverySource(duplicate, clock=_clock).discover()
    with pytest.raises(DiscoveryPayloadError, match="root.json"):
        JsonDiscoverySource(root, clock=_clock).discover()


def test_json_rejects_excessive_nesting_as_sanitized_payload_error(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    nested = "[" * 200 + "0" + "]" * 200
    path.write_text(
        '[{"url":"https://x.test","business_name":"X","metadata":{"deep":' + nested + "}}]",
        encoding="utf-8",
    )
    with pytest.raises(DiscoveryPayloadError, match="invalid discovery file: deep.json"):
        JsonDiscoverySource(path).discover()


def test_nested_metadata_recursion_is_sanitized(tmp_path: Path) -> None:
    path = tmp_path / "deep-metadata.csv"
    nested = "[" * 2_000 + "0" + "]" * 2_000
    path.write_text(
        'url,business_name,metadata\nhttps://x.test,X,""' + nested.replace('"', '""') + '""\n',
        encoding="utf-8",
    )

    with pytest.raises(DiscoveryPayloadError, match="invalid discovery file: deep-metadata.csv"):
        CsvDiscoverySource(path, max_field_size=10_000).discover()
    with pytest.raises(DiscoveryPayloadError, match="invalid manual record"):
        ManualUrlDiscoverySource(
            [{"url": "https://x.test", "business_name": "X", "metadata": nested}]
        ).discover()


def test_manual_metadata_parser_recursion_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recurse(_text: str) -> object:
        raise RecursionError("parser detail must not escape")

    monkeypatch.setattr(batch_module, "_strict_json", recurse)
    with pytest.raises(DiscoveryPayloadError, match="invalid manual record") as raised:
        ManualUrlDiscoverySource(
            [{"url": "https://x.test", "business_name": "X", "metadata": "{}"}]
        ).discover()

    assert "parser detail" not in str(raised.value)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("location", ["coordinate", "metadata"])
def test_json_rejects_nonstandard_constants_at_every_level(
    tmp_path: Path, constant: str, location: str
) -> None:
    path = tmp_path / "constant.json"
    field = (
        f'"latitude":{constant},"longitude":1'
        if location == "coordinate"
        else f'"metadata":{{"nested":[{constant}]}}'
    )
    path.write_text(f'[{{"url":"https://x.test","business_name":"X",{field}}}]', encoding="utf-8")

    with pytest.raises(
        DiscoveryPayloadError, match="invalid discovery file: constant.json"
    ) as raised:
        JsonDiscoverySource(path, clock=_clock).discover()

    assert constant not in str(raised.value)


class _Repository:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def upsert_discovery_seed(self, seed: CandidateSeed, cohort: str) -> DiscoverySeedUpsertResult:
        self.urls.append(str(seed.url))
        return DiscoverySeedUpsertResult(
            DiscoverySeedDisposition.INSERTED, f"candidate:{seed.url}"
        )


class _DuplicateRepository(_Repository):
    def upsert_discovery_seed(self, seed: CandidateSeed, cohort: str) -> DiscoverySeedUpsertResult:
        self.urls.append(str(seed.url))
        return DiscoverySeedUpsertResult(
            DiscoverySeedDisposition.DUPLICATE, f"candidate:{seed.url}"
        )


def _seed(url: str, *, latitude: float | None, longitude: float | None, hint: str) -> CandidateSeed:
    return CandidateSeed(
        url=url,
        business_name="Nhà máy Thăng Long",
        source_url=url,
        source_type="manual",
        discovered_at=NOW,
        latitude=latitude,
        longitude=longitude,
        industry_hint=hint,
    )


def test_service_geofences_tags_upserts_and_keeps_rejected_outcomes() -> None:
    repository = _Repository()
    service = DiscoveryService(
        geofence=GeofenceService(21.0278, 105.8342, 80.0, fallback_dataset=None),
        repository=repository,
    )

    result = service.process(
        [
            _seed(
                "https://inside.example.com", latitude=21.03, longitude=105.83, hint="manufacturer"
            ),
            _seed(
                "https://outside.example.net", latitude=10.8, longitude=106.6, hint="manufacturer"
            ),
            _seed(
                "https://unknown.example.org", latitude=None, longitude=None, hint="manufacturer"
            ),
        ]
    )

    assert len(result.candidates) == 1
    assert repository.urls == ["https://inside.example.com/"]
    assert [(item.status, item.reason) for item in result.outcomes] == [
        ("accepted", "coordinates_within_radius"),
        ("outside", "coordinates_outside_radius"),
        ("unresolved", "fallback_unconfigured"),
    ]
    assert result.outcomes[0].cohort == "manufacturer"


def test_service_cap_and_readiness_matrix() -> None:
    service = DiscoveryService(max_seed_candidates=1)
    seeds = [
        _seed("https://a.example.com", latitude=None, longitude=None, hint="other"),
        _seed("https://b.example.net", latitude=None, longitude=None, hint="other"),
    ]

    assert len(service.normalize_unique(seeds)) == 1
    status = service.readiness()
    assert status.manual_sources == "ready"
    assert status.geofence == "not_ready"
    assert status.automatic_discovery == "not_ready"
    assert status.providers == ()


def test_service_rejects_infinite_duplicate_input_after_bounded_consumption() -> None:
    consumed = 0

    def seeds() -> Iterator[CandidateSeed]:
        nonlocal consumed
        seed = _seed("https://same.example.com", latitude=None, longitude=None, hint="other")
        while True:
            consumed += 1
            yield seed

    service = DiscoveryService(max_seed_candidates=2, max_input_seeds=5)
    with pytest.raises(DiscoveryPayloadError, match="too many candidate seeds"):
        service.normalize_unique(seeds())
    assert consumed == 6


def test_service_merges_richer_same_domain_evidence_independent_of_input_order() -> None:
    serper = _seed(
        "https://example.com", latitude=None, longitude=None, hint="other"
    ).validated_replace(
        business_name="Example",
        source_type="serper",
        source_url="https://google.com/search?q=example",
        metadata={"provider": "serper"},
    )
    places = serper.validated_replace(
        url="https://www.example.com/about",
        business_name="Example Company",
        source_type="google-places",
        source_url="https://maps.google.com/?cid=1",
        address="Hà Nội",
        latitude=21.03,
        longitude=105.83,
        external_id="place-1",
        metadata={"provider": "google-places"},
    )
    first = DiscoveryService().normalize_unique([serper, places])[0]
    second = DiscoveryService().normalize_unique([places, serper])[0]
    for seed in (first, second):
        assert (seed.latitude, seed.longitude, seed.address, seed.external_id) == (
            21.03,
            105.83,
            "Hà Nội",
            "place-1",
        )
        assert {item["source_type"] for item in seed.metadata["source_observations"]} == {
            "serper",
            "google-places",
        }
        assert {item["metadata"]["provider"] for item in seed.metadata["source_observations"]} == {
            "serper",
            "google-places",
        }
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_service_upserts_duplicate_evidence_and_marks_outcome() -> None:
    repository = _DuplicateRepository()
    seed = _seed(
        "https://duplicate.example.com", latitude=None, longitude=None, hint="manufacturer"
    )

    result = DiscoveryService(
        geofence=GeofenceService(21.0278, 105.8342, 80.0, fallback_dataset=None),
        repository=repository,
    ).process([seed.validated_replace(latitude=21.03, longitude=105.83)])

    assert repository.urls == ["https://duplicate.example.com/"]
    assert result.outcomes[0].status == "duplicate"
    assert result.candidates == ()


def test_service_requires_geofence_before_repository_mutation() -> None:
    repository = _Repository()
    service = DiscoveryService(repository=repository)

    with pytest.raises(DiscoveryConfigurationError, match="geofence is not configured"):
        service.process(
            [_seed("https://inside.example.com", latitude=21.03, longitude=105.83, hint="other")]
        )

    assert repository.urls == []


def test_readiness_reports_configured_provider_without_credentials() -> None:
    class Provider:
        name = "serper"

        def readiness(self) -> str:
            return "ready"

    status = DiscoveryService(providers=[Provider()]).readiness()

    assert status.automatic_discovery == "ready"
    assert status.geofence == "not_ready"
    assert status.providers == (("serper", "ready"),)
    assert "key" not in repr(status).lower()
