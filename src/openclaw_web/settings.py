"""Validated configuration loading for workflow markets."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field


class MarketCenter(BaseModel):
    """Geographic center used to evaluate a market radius."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class MarketConfig(BaseModel):
    """Validated settings for a discovery market."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    market_id: str = Field(min_length=1)
    center: MarketCenter
    radius_km: float = Field(gt=0)
    industries: list[str] = Field(min_length=1)
    timezone: str = Field(min_length=1)


def load_market(path: Path) -> MarketConfig:
    """Load and validate a market definition from YAML."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return MarketConfig.model_validate(payload)
