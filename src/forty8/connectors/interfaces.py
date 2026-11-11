"""Interfaces for every external system. Agents depend only on these Protocols;
implementations (mock today, real later) are chosen in config/forty8.toml."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from forty8.models.exposure import AddressQuery, BuildingAttributes, ExposureSet, GeocodeResult, SearchHit
from forty8.models.results import (
    JobStatus,
    LLMResponse,
    LossResults,
    PricingResponse,
    PricingSubmission,
    PriorYearSubmission,
    SurveyRecord,
)


@runtime_checkable
class Geocoder(Protocol):
    name: str

    def geocode(self, address: AddressQuery) -> GeocodeResult: ...


@runtime_checkable
class WebSearch(Protocol):
    def search(self, query: str) -> list[SearchHit]: ...


@runtime_checkable
class BuildingData(Protocol):
    name: str

    def lookup(self, latitude: float, longitude: float) -> BuildingAttributes | None: ...


@runtime_checkable
class HazardData(Protocol):
    name: str

    def lookup(self, latitude: float, longitude: float) -> dict[str, float] | None: ...


@runtime_checkable
class H3Indexer(Protocol):
    def cell(self, latitude: float, longitude: float, resolution: int) -> str: ...


@runtime_checkable
class SurveyStore(Protocol):
    def get(self, insured: str, loc_number: str) -> SurveyRecord | None: ...


@runtime_checkable
class PriorYearStore(Protocol):
    def get(self, insured: str) -> PriorYearSubmission | None: ...


@runtime_checkable
class CatModel(Protocol):
    name: str

    def submit(self, exposure: ExposureSet) -> str: ...

    def status(self, job_id: str) -> JobStatus: ...

    def results(self, job_id: str) -> LossResults: ...


@runtime_checkable
class PricingTool(Protocol):
    def submit(self, submission: PricingSubmission) -> PricingResponse: ...


@runtime_checkable
class LLMProvider(Protocol):
    """Shaped so a Claude (Anthropic SDK) implementation can be dropped in: a named task,
    instructions, the content to read, and a JSON schema for the structured answer."""

    model: str

    def complete(self, task: str, instructions: str, content: str, schema: dict[str, Any] | None = None) -> LLMResponse: ...


@dataclass
class Connectors:
    moodys_geocoder: Geocoder
    bing_geocoder: Geocoder
    web_search: WebSearch
    building_data: BuildingData
    nathan: HazardData
    athenium: HazardData
    h3: H3Indexer
    survey_store: SurveyStore
    prior_year_store: PriorYearStore
    cat_model: CatModel
    pricing_tool: PricingTool
    llm: LLMProvider
