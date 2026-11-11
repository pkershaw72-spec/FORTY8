"""Submission, exposure and geocoding models. Location and Account use OED field names."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Submission(BaseModel):
    run_id: str
    broker: str
    sov_path: str
    slip_path: str
    insured: str | None = None
    languages: list[str] = Field(default_factory=list)


class Lineage(BaseModel):
    """Where a row came from in the broker's file."""

    tab: str
    source_row: int


class GeocodeResolution(StrEnum):
    ROOFTOP = "rooftop"
    STREET = "street"
    POSTCODE = "postcode"
    CITY = "city"
    UNMATCHED = "unmatched"


class AddressQuery(BaseModel):
    street: str = ""
    city: str = ""
    area: str = ""
    postal_code: str = ""
    country: str = ""

    def one_line(self) -> str:
        return ", ".join(p for p in (self.street, self.city, self.area, self.postal_code, self.country) if p)


class GeocodeResult(BaseModel):
    resolution: GeocodeResolution
    provider: str
    confidence: float = Field(ge=0, le=1)
    latitude: float | None = None
    longitude: float | None = None
    matched_address: str | None = None


class SearchHit(BaseModel):
    title: str
    snippet: str
    url: str


class SourcedValue(BaseModel):
    """A value plus where it came from; used for every enriched field."""

    value: Any
    source: str
    confidence: float = Field(ge=0, le=1, default=1.0)


class BuildingAttributes(BaseModel):
    year_built: int | None = None
    number_of_storeys: int | None = None
    construction_code: int | None = None
    occupancy_code: int | None = None
    floor_area_m2: float | None = None


class Location(BaseModel):
    # OED location fields
    LocNumber: str
    LocName: str | None = None
    StreetAddress: str | None = None
    City: str | None = None
    AreaCode: str | None = None
    PostalCode: str | None = None
    CountryCode: str | None = None
    Latitude: float | None = None
    Longitude: float | None = None
    OccupancyCode: int | None = None
    ConstructionCode: int | None = None
    YearBuilt: int | None = None
    NumberOfStoreys: int | None = None
    FloorArea: float | None = None      # square metres
    BuildingTIV: float = 0.0
    ContentsTIV: float = 0.0
    BITIV: float = 0.0
    LocCurrency: str | None = None
    # Working fields (not exported to OED)
    occupancy_text: str | None = None
    construction_text: str | None = None
    stated_tiv: float | None = None     # the row's own total column, if the SoV has one
    lineage: Lineage | None = None
    geocode: GeocodeResult | None = None
    h3_cell: str | None = None
    hazard: dict[str, SourcedValue] = Field(default_factory=dict)
    sources: dict[str, SourcedValue] = Field(default_factory=dict)  # field name -> provenance
    zones: list[str] = Field(default_factory=list)

    @property
    def tiv(self) -> float:
        return self.BuildingTIV + self.ContentsTIV + self.BITIV


class Sublimit(BaseModel):
    peril: str
    zone: str          # "all" or a zone key such as "us_tier1_wind"
    limit: float


class Account(BaseModel):
    """OED account/policy level terms coded from the slip."""

    AccNumber: str
    AccName: str | None = None
    AccCurrency: str | None = None
    PolInceptionDate: str | None = None
    PolExpiryDate: str | None = None
    PolPerilsCovered: list[str] = Field(default_factory=list)
    PolLimit: float | None = None
    PolDed: float | None = None
    sublimits: list[Sublimit] = Field(default_factory=list)


class ExposureSet(BaseModel):
    account: Account
    locations: list[Location]

    @property
    def total_tiv(self) -> float:
        return sum(loc.tiv for loc in self.locations)
