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


class GeocodingSettings(BaseModel):
    street_level_or_better: list[str]
    h3_resolution: int


class ModelSettings(BaseModel):
    max_retries: int
    poll_interval_seconds: float
    timeout_seconds: float


class StorageSettings(BaseModel):
    db_path: str
    mock_data_dir: str


class SchemaSettings(BaseModel):
    target: str


class FxSettings(BaseModel):
    base: str
    rates_to_base: dict[str, float]


class ConnectorConfig(BaseModel):
    impl: str
    endpoint_env: str | None = None
    api_key_env: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    def endpoint(self) -> str | None:
        return os.environ.get(self.endpoint_env) if self.endpoint_env else None

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None


class Settings(BaseModel):
    thresholds: Thresholds
    geocoding: GeocodingSettings
    model: ModelSettings
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
    if overrides:
        _deep_merge(raw, overrides)
    if db := os.environ.get("FORTY8_DB"):
        raw["storage"]["db_path"] = db
    return Settings.model_validate(raw)


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
