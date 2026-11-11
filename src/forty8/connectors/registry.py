"""Builds the connector set from config. To add a real connector, register a factory
under a new impl name here and select it in config/forty8.toml."""

from __future__ import annotations

from typing import Any, Callable

from forty8.config import Settings
from forty8.connectors.h3_indexer import H3LibIndexer
from forty8.connectors.interfaces import Connectors
from forty8.connectors.mocks.data import (
    MockAthenium,
    MockBuildingData,
    MockNathan,
    MockPriorYearStore,
    MockSurveyStore,
)
from forty8.connectors.mocks.geocoders import MockBingGeocoder, MockMoodysGeocoder, MockWebSearch
from forty8.connectors.mocks import llm_coding, llm_headers, llm_slip  # noqa: F401  (register mock LLM tasks)
from forty8.connectors.mocks.llm import MockLLM
from forty8.connectors.mocks.model import MockCatModel, MockPricingTool

Factory = Callable[[dict[str, Any]], Any]

FACTORIES: dict[tuple[str, str], Factory] = {
    ("moodys_geocoder", "mock"): MockMoodysGeocoder,
    ("bing_geocoder", "mock"): MockBingGeocoder,
    ("web_search", "mock"): MockWebSearch,
    ("building_data", "mock"): MockBuildingData,
    ("nathan", "mock"): MockNathan,
    ("athenium", "mock"): MockAthenium,
    ("h3", "h3"): H3LibIndexer,
    ("survey_store", "mock"): MockSurveyStore,
    ("prior_year_store", "mock"): MockPriorYearStore,
    ("cat_model", "mock"): MockCatModel,
    ("pricing_tool", "mock"): MockPricingTool,
    ("llm", "mock"): MockLLM,
}

_FILE_BACKED = {"survey_store", "prior_year_store"}


def build_connectors(settings: Settings) -> Connectors:
    built: dict[str, Any] = {}
    for name in Connectors.__dataclass_fields__:
        cfg = settings.connectors.get(name)
        if cfg is None:
            raise KeyError(f"no [connectors.{name}] section in config")
        factory = FACTORIES.get((name, cfg.impl))
        if factory is None:
            known = sorted(impl for (n, impl) in FACTORIES if n == name)
            raise KeyError(f"connector '{name}' has no implementation '{cfg.impl}' (available: {known})")
        options = dict(cfg.options)
        if name in _FILE_BACKED:
            options.setdefault("data_dir", settings.storage.mock_data_dir)
        built[name] = factory(options)
    return Connectors(**built)
