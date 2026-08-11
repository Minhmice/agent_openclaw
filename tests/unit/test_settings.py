from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from openclaw_web.settings import load_market

CANONICAL_MARKET: dict[str, Any] = {
    "market_id": "hanoi-80km",
    "center": {
        "name": "Hà Nội",
        "latitude": 21.0285,
        "longitude": 105.8542,
    },
    "radius_km": 80,
    "industries": ["*"],
    "timezone": "Asia/Bangkok",
}


def _load_payload(tmp_path: Path, payload: dict[str, Any]) -> Any:
    market_file = tmp_path / "market.yaml"
    market_file.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return load_market(market_file)


def _with_value(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    payload = deepcopy(CANONICAL_MARKET)
    target: dict[str, Any] = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return payload


def test_hanoi_market_has_full_canonical_configuration() -> None:
    market = load_market(Path("config/markets/hanoi-80km.yaml"))

    assert market.model_dump() == CANONICAL_MARKET
    assert market.market_id == "hanoi-80km"
    assert market.center.name == "Hà Nội"
    assert market.center.latitude == 21.0285
    assert market.center.longitude == 105.8542
    assert market.radius_km == 80
    assert market.industries == ["*"]
    assert market.timezone == "Asia/Bangkok"


def test_market_text_values_are_normalized(tmp_path: Path) -> None:
    payload = deepcopy(CANONICAL_MARKET)
    payload["market_id"] = "  hanoi-80km  "
    payload["center"]["name"] = "  Hà Nội  "
    payload["industries"] = ["  retail  ", " hospitality "]
    payload["timezone"] = "  Asia/Bangkok  "

    market = _load_payload(tmp_path, payload)

    assert market.market_id == "hanoi-80km"
    assert market.center.name == "Hà Nội"
    assert market.industries == ["retail", "hospitality"]
    assert market.timezone == "Asia/Bangkok"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param(("market_id",), "", id="empty-market-id"),
        pytest.param(("market_id",), "   ", id="blank-market-id"),
        pytest.param(("center", "name"), "", id="empty-center-name"),
        pytest.param(("center", "name"), "\t  ", id="blank-center-name"),
        pytest.param(("industries",), [], id="empty-industries"),
        pytest.param(("industries",), [""], id="empty-industry"),
        pytest.param(("industries",), ["  "], id="blank-industry"),
        pytest.param(("industries",), ["retail", "  "], id="blank-industry-among-valid"),
        pytest.param(("timezone",), "", id="empty-timezone"),
        pytest.param(("timezone",), "   ", id="blank-timezone"),
    ],
)
def test_market_rejects_blank_required_text(
    tmp_path: Path, path: tuple[str, ...], value: Any
) -> None:
    with pytest.raises(ValidationError):
        _load_payload(tmp_path, _with_value(path, value))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param(("center", "latitude"), -90.0001, id="latitude-too-low"),
        pytest.param(("center", "latitude"), 90.0001, id="latitude-too-high"),
        pytest.param(("center", "longitude"), -180.0001, id="longitude-too-low"),
        pytest.param(("center", "longitude"), 180.0001, id="longitude-too-high"),
        pytest.param(("radius_km",), 0, id="zero-radius"),
        pytest.param(("radius_km",), -1, id="negative-radius"),
    ],
)
def test_market_rejects_out_of_range_numbers(
    tmp_path: Path, path: tuple[str, ...], value: float
) -> None:
    with pytest.raises(ValidationError):
        _load_payload(tmp_path, _with_value(path, value))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param(("center", "latitude"), "21.0285", id="coerced-latitude"),
        pytest.param(("center", "longitude"), "105.8542", id="coerced-longitude"),
        pytest.param(("radius_km",), "80", id="coerced-radius"),
        pytest.param(("center", "latitude"), float("nan"), id="nan-latitude"),
        pytest.param(("center", "longitude"), float("inf"), id="infinite-longitude"),
        pytest.param(("radius_km",), float("-inf"), id="infinite-radius"),
    ],
)
def test_market_rejects_non_strict_or_non_finite_numbers(
    tmp_path: Path, path: tuple[str, ...], value: Any
) -> None:
    with pytest.raises(ValidationError):
        _load_payload(tmp_path, _with_value(path, value))


@pytest.mark.parametrize("timezone", ["Not/AZone", "UTC+07:00", "Asia/Does_Not_Exist"])
def test_market_rejects_unrecognized_timezone(tmp_path: Path, timezone: str) -> None:
    with pytest.raises(ValidationError):
        _load_payload(tmp_path, _with_value(("timezone",), timezone))


@pytest.mark.parametrize("path", [("unknown",), ("center", "unknown")])
def test_market_rejects_unknown_keys(tmp_path: Path, path: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        _load_payload(tmp_path, _with_value(path, True))
