"""Strict bounded manual, CSV, and JSON candidate sources."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from openclaw_web.discovery.base import Clock, DiscoveryPayloadError, normalized_clock, utc_now
from openclaw_web.models import CandidateSeed

_REQUIRED = frozenset({"url", "business_name"})
_OPTIONAL = frozenset(
    {
        "source_url",
        "seed_id",
        "address",
        "latitude",
        "longitude",
        "industry_hint",
        "external_id",
        "discovered_at",
        "metadata",
    }
)
_COLUMNS = _REQUIRED | _OPTIONAL
_DEFAULT_MAX_BYTES = 2_000_000
_DEFAULT_MAX_ITEMS = 2_000
_DEFAULT_FIELD_SIZE = 64_000


def _strict_json(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid timestamp")
    parsed = (
        datetime.fromisoformat(value.strip().removesuffix("Z") + "+00:00")
        if value.strip().endswith("Z")
        else datetime.fromisoformat(value.strip())
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(UTC)


def _coordinate(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise TypeError("coordinates cannot be booleans")
    if isinstance(value, str):
        return float(value)
    if not isinstance(value, int | float):
        raise TypeError("coordinates must be numbers")
    return float(value)


def _metadata(value: object) -> Mapping[str, Any]:
    if value is None or value == "":
        return {}
    parsed = _strict_json(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise TypeError("metadata must be an object")
    return parsed


def _record_seed(
    raw: Mapping[str, object],
    *,
    source_type: str,
    clock: Clock,
    error_label: str,
) -> CandidateSeed:
    try:
        keys = set(raw)
        if not _REQUIRED <= keys or not keys <= _COLUMNS:
            raise ValueError("invalid columns")
        data = {key: value for key, value in raw.items() if value not in (None, "")}
        url = data.get("url")
        business_name = data.get("business_name")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("invalid URL")
        if not isinstance(business_name, str) or not business_name.strip():
            raise ValueError("invalid business name")
        latitude = _coordinate(data.get("latitude"))
        longitude = _coordinate(data.get("longitude"))
        if (latitude is None) != (longitude is None):
            raise ValueError("coordinates must be paired")
        discovered = (
            _parse_timestamp(data["discovered_at"])
            if "discovered_at" in data
            else normalized_clock(clock)
        )
        return CandidateSeed.model_validate(
            {
                **data,
                "url": url.strip(),
                "business_name": business_name.strip(),
                "source_url": data.get("source_url", url.strip()),
                "source_type": source_type,
                "discovered_at": discovered,
                "latitude": latitude,
                "longitude": longitude,
                "metadata": _metadata(data.get("metadata")),
            }
        )
    except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
        raise DiscoveryPayloadError(error_label) from None


class ManualUrlDiscoverySource:
    def __init__(self, records: Sequence[Mapping[str, object]], *, clock: Clock = utc_now) -> None:
        if isinstance(records, str | bytes | bytearray):
            raise TypeError("manual records must be a non-string sequence")
        self._records = tuple(records)
        self._clock = clock

    def discover(self) -> tuple[CandidateSeed, ...]:
        return tuple(
            _record_seed(
                record,
                source_type="manual",
                clock=self._clock,
                error_label="invalid manual record",
            )
            for record in self._records
        )


class _FileSource:
    def __init__(
        self,
        path: Path,
        *,
        clock: Clock = utc_now,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        max_items: int = _DEFAULT_MAX_ITEMS,
        allow_utf8_bom: bool = True,
    ) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items <= 0:
            raise ValueError("max_items must be positive")
        self._path = Path(path)
        self._clock = clock
        self._max_bytes = max_bytes
        self._max_items = max_items
        self._allow_bom = allow_utf8_bom

    def _text(self) -> str:
        try:
            data = self._path.read_bytes()
            if len(data) > self._max_bytes:
                raise ValueError
            if data.startswith(b"\xef\xbb\xbf"):
                if not self._allow_bom:
                    raise ValueError
                data = data[3:]
            return data.decode("utf-8", errors="strict")
        except (OSError, UnicodeError, ValueError):
            raise DiscoveryPayloadError(f"invalid discovery file: {self._path.name}") from None

    def _seeds(
        self, records: Sequence[Mapping[str, object]], source_type: str
    ) -> tuple[CandidateSeed, ...]:
        if len(records) > self._max_items:
            raise DiscoveryPayloadError(f"invalid discovery file: {self._path.name}")
        return tuple(
            _record_seed(
                record,
                source_type=source_type,
                clock=self._clock,
                error_label=f"invalid discovery file: {self._path.name}",
            )
            for record in records
        )


class CsvDiscoverySource(_FileSource):
    def __init__(
        self, *args: Any, max_field_size: int = _DEFAULT_FIELD_SIZE, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        if (
            isinstance(max_field_size, bool)
            or not isinstance(max_field_size, int)
            or max_field_size <= 0
        ):
            raise ValueError("max_field_size must be positive")
        self._max_field_size = max_field_size

    def discover(self) -> tuple[CandidateSeed, ...]:
        try:
            text = self._text()
            lines = text.splitlines()
            first_line = lines[0] if lines else ""
            if any(not line.strip() for line in lines[1:]):
                raise ValueError
            headers = next(csv.reader([first_line]))
            if (
                not _REQUIRED <= set(headers)
                or not set(headers) <= _COLUMNS
                or len(headers) != len(set(headers))
            ):
                raise ValueError
            old_limit = csv.field_size_limit()
            try:
                csv.field_size_limit(self._max_field_size)
                reader = csv.DictReader(io.StringIO(text, newline=""))
                rows = list(reader)
            finally:
                csv.field_size_limit(old_limit)
            if reader.fieldnames != headers or any(None in row for row in rows):
                raise ValueError
            if any(
                not row or all(value is None or not value.strip() for value in row.values())
                for row in rows
            ):
                raise ValueError
            return self._seeds(rows, "csv")
        except DiscoveryPayloadError:
            raise
        except (csv.Error, IndexError, TypeError, ValueError):
            raise DiscoveryPayloadError(f"invalid discovery file: {self._path.name}") from None


class JsonDiscoverySource(_FileSource):
    def discover(self) -> tuple[CandidateSeed, ...]:
        try:
            payload = _strict_json(self._text())
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise ValueError
            return self._seeds(payload, "json")
        except DiscoveryPayloadError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError):
            raise DiscoveryPayloadError(f"invalid discovery file: {self._path.name}") from None
