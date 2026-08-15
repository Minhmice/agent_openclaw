"""Validated configuration loading for workflow markets."""

from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class MarketCenter(BaseModel):
    """Geographic center used to evaluate a market radius."""

    model_config = ConfigDict(extra="forbid")

    name: NonBlankString
    latitude: float = Field(strict=True, allow_inf_nan=False, ge=-90, le=90)
    longitude: float = Field(strict=True, allow_inf_nan=False, ge=-180, le=180)


class MarketConfig(BaseModel):
    """Validated settings for a discovery market."""

    model_config = ConfigDict(extra="forbid")

    market_id: NonBlankString
    center: MarketCenter
    radius_km: float = Field(strict=True, allow_inf_nan=False, gt=0)
    industries: list[NonBlankString] = Field(min_length=1)
    timezone: NonBlankString

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Require a timezone recognized by the installed IANA database."""

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            if not available_timezones():
                raise ValueError("IANA timezone database is unavailable; install tzdata") from exc
            raise ValueError(f"unrecognized IANA timezone: {value}") from exc
        return value


def load_market(path: Path) -> MarketConfig:
    """Load and validate a market definition from YAML."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return MarketConfig.model_validate(payload)
