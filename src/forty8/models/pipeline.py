"""Typed outputs of steps 1-5 and the slip lane."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forty8.models.exposure import ExposureSet, Lineage, Location
from forty8.models.results import LossResults, PricingResponse, PricingSubmission


# ---- step 1: intake
class RawSheet(BaseModel):
    name: str
    rows: list[list[Any]]


class IntakeOutput(BaseModel):
    insured: str | None = None
    sov_path: str
    slip_path: str
    sheets: list[RawSheet] = Field(default_factory=list)
    slip_text: str = ""
    sheet_languages: dict[str, str] = Field(default_factory=dict)
    slip_language: str = "unknown"
    languages: list[str] = Field(default_factory=list)


# ---- step 2: cleaning
class CleanRow(BaseModel):
    lineage: Lineage
    values: dict[str, Any]  # source header -> cell value


class StatedTotal(BaseModel):
    """A subtotal or total row removed from the data, kept so step 8 can reconcile TIV."""

    tab: str
    kind: str  # "subtotal" | "total"
    label: str
    source_row: int
    values: dict[str, Any]


class TabSummary(BaseModel):
    tab: str
    header_row: int | None = None  # 1-based row in the source sheet
    headers: list[str] = Field(default_factory=list)
    data_rows: int = 0
    title_rows_removed: int = 0
    blank_rows_removed: int = 0
    subtotal_rows_removed: int = 0
    skipped: bool = False
    skip_reason: str = ""


class CleanTable(BaseModel):
    rows: list[CleanRow] = Field(default_factory=list)
    tabs: list[TabSummary] = Field(default_factory=list)
    stated_totals: list[StatedTotal] = Field(default_factory=list)


# ---- step 3: schema mapping
class ColumnMapping(BaseModel):
    tab: str
    source_header: str
    target_field: str | None  # canonical field, None = not mapped (ignored)
    confidence: float
    method: str
    unit: str | None = None


class MappedRow(BaseModel):
    lineage: Lineage
    values: dict[str, Any]  # canonical field -> value


class MappedTable(BaseModel):
    target_schema: str
    mappings: list[ColumnMapping] = Field(default_factory=list)
    rows: list[MappedRow] = Field(default_factory=list)


# ---- steps 4 and 5: coding and ML classification
class PendingCode(BaseModel):
    """A free-text value the rule tables could not code; step 5 classifies it."""

    field: str  # "occupancy" | "construction"
    text: str
    row_indexes: list[int]


class CodedLocations(BaseModel):
    locations: list[Location] = Field(default_factory=list)
    pending: list[PendingCode] = Field(default_factory=list)


class Classification(BaseModel):
    field: str
    text: str
    label: str
    code: int
    confidence: float
    method: str


class ClassifiedLocations(BaseModel):
    locations: list[Location] = Field(default_factory=list)
    classifications: list[Classification] = Field(default_factory=list)


# ---- step 2b: contract (slip lane)
class SlipTerm(BaseModel):
    name: str               # e.g. "limit", "inception", "sublimit:earthquake:california"
    value: Any = None
    confidence: float = Field(ge=0, le=1)
    source_text: str = ""
    method: str = ""


class CodedSlip(BaseModel):
    terms: list[SlipTerm] = Field(default_factory=list)
    overall_confidence: float = 0.0   # minimum term confidence
    threshold: float = 0.0
    flagged: list[str] = Field(default_factory=list)

    def get(self, name: str) -> SlipTerm | None:
        return next((t for t in self.terms if t.name == name), None)

    def value(self, name: str) -> Any:
        term = self.get(name)
        return term.value if term else None

    @property
    def sublimits(self) -> list[SlipTerm]:
        return [t for t in self.terms if t.name.startswith("sublimit:")]


# ---- step 6: geocoding
class GeocodedLocations(BaseModel):
    locations: list[Location] = Field(default_factory=list)
    tiv_street_share: float = 0.0     # share of TIV (base currency) at street level or better
    threshold: float = 0.0
    total_tiv_base: float = 0.0
    by_resolution: dict[str, int] = Field(default_factory=dict)
    by_provider: dict[str, int] = Field(default_factory=dict)


# ---- step 7: enrichment
class YoYFlag(BaseModel):
    loc_number: str
    prior_tiv: float
    current_tiv: float
    change: float             # +1.5 = up 150%
    currency: str | None = None
    resolution: str = "open"  # open | confirmed | reverted to prior


class EnrichedLocations(BaseModel):
    locations: list[Location] = Field(default_factory=list)
    completeness_before: float = 0.0
    completeness_after: float = 0.0
    completeness_tiv_weighted: float = 0.0
    completeness_by_field: dict[str, float] = Field(default_factory=dict)
    filled_by_source: dict[str, int] = Field(default_factory=dict)
    yoy_flags: list[YoYFlag] = Field(default_factory=list)
    new_locations: list[str] = Field(default_factory=list)
    dropped_locations: list[str] = Field(default_factory=list)


# ---- step 8: audit gate
class QualityTest(BaseModel):
    name: str
    passed: bool
    detail: str
    failures: int = 0
    waived: int = 0   # failures an analyst accepted


class AuditGateOutput(BaseModel):
    locations: list[Location] = Field(default_factory=list)
    tests: list[QualityTest] = Field(default_factory=list)


# ---- step 9: policy mapping
class PolicyMapping(BaseModel):
    exposure: ExposureSet
    zone_resolution: dict[str, str] = Field(default_factory=dict)   # slip wording -> zone key
    locations_by_zone: dict[str, int] = Field(default_factory=dict)


# ---- step 10: model and track
class ModelOutput(BaseModel):
    job_id: str
    attempts: int
    model: str
    results: LossResults
    total_tiv: float
    currency: str


# ---- step 11: pricing
class PricingOutput(BaseModel):
    submission: PricingSubmission
    response: PricingResponse
