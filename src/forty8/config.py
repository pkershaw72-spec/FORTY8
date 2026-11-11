"""Typed settings loaded from the single config file (config/forty8.toml)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "forty8.toml"


class Thresholds(BaseModel):
    geocode_min_tiv_street_level: float = Field(ge=0, le=1)
    slip_min_confidence: float = Field(ge=0, le=1)
    ml_min_confidence: float = Field(ge=0, le=1)
    header_min_confidence: float = Field(ge=0, le=1)
    yoy_tiv_change_flag: float = Field(ge=0)


class GeocodingSettings(BaseModel):
    street_level_or_better: list[str]
    h3_resolution: int
    web_max_distance_km: float


class AuditSettings(BaseModel):
    year_built_min: int
    year_built_max_offset_years: int
    tiv_reconcile_tolerance: float
    max_storeys_by_construction: dict[str, int]


class ModelSettings(BaseModel):
    max_retries: int
    poll_interval_seconds: float
    timeout_seconds: float


class MetricsSettings(BaseModel):
    target_minutes: float


class StorageSettings(BaseModel):
    db_path: str
    mock_data_dir: str


class SchemaSettings(BaseModel):
    target: str


class ConnectorConfig(BaseModel):
    impl: str
    endpoint_env: str | None = None
    api_key_env: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    def endpoint(self) -> str | None:
        return os.environ.get(self.endpoint_env) if self.endpoint_env else None

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None


class FxSettings(BaseModel):
    base: str
    rates_to_base: dict[str, float]

    def to_base(self, amount: float, currency: str | None) -> float:
        """Convert to the base currency. An unknown or missing currency is treated as base."""
        return amount * self.rates_to_base.get((currency or self.base).upper(), 1.0)

    def known(self, currency: str | None) -> bool:
        return currency is not None and currency.upper() in self.rates_to_base


class Settings(BaseModel):
    thresholds: Thresholds
    geocoding: GeocodingSettings
    audit: AuditSettings
    model: ModelSettings
    metrics: MetricsSettings
    storage: StorageSettings
    schema_: SchemaSettings = Field(alias="schema")
    fx: FxSettings
    connectors: dict[str, ConnectorConfig]


def config_path() -> Path:
    """FORTY8_CONFIG, else ./config/forty8.toml, else the file shipped with the repo."""
    if env := os.environ.get("FORTY8_CONFIG"):
        return Path(env)
    local = Path.cwd() / "config" / "forty8.toml"
    return local if local.exists() else DEFAULT_CONFIG_PATH


def load_settings(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    """Load settings. `overrides` is a nested dict merged over the file (used by tests)."""
    with open(path or config_path(), "rb") as fh:
        raw = tomllib.load(fh)
    if db := os.environ.get("FORTY8_DB"):
        raw["storage"]["db_path"] = db
    if overrides:  # explicit overrides win over the environment
        _deep_merge(raw, overrides)
    return Settings.model_validate(raw)


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
