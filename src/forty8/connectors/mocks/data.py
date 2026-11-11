"""Mock third-party data: building attributes, NATHAN and Athenium hazard, survey and prior-year stores."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from forty8.models.exposure import BuildingAttributes
from forty8.models.results import PriorYearSubmission, SurveyRecord
from forty8.reference.codes import CONSTRUCTION_CODES
from forty8.reference.gazetteer import fold, nearest_city


def _unit(*parts: object) -> float:
    """Deterministic pseudo-random number in [0, 1) from the inputs."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


def slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in fold(text)).strip("_")


class MockBuildingData:
    """Covers about 80% of coordinates; attributes are a deterministic function of position."""

    name = "building_data"

    def __init__(self, options: dict | None = None):
        self.coverage = float((options or {}).get("coverage", 0.8))

    def lookup(self, latitude: float, longitude: float) -> BuildingAttributes | None:
        key = (round(latitude, 5), round(longitude, 5))
        if _unit("cover", *key) >= self.coverage:
            return None
        construction = ["masonry", "reinforced_concrete", "steel", "light_metal"][int(_unit("con", *key) * 4)]
        return BuildingAttributes(
            year_built=1960 + int(_unit("yr", *key) * 60),
            number_of_storeys=1 + int(_unit("st", *key) * 8),
            construction_code=CONSTRUCTION_CODES[construction],
            floor_area_m2=round(500 + _unit("ar", *key) * 20000, 0),
        )


_NATHAN_BY_ZONE = {
    "us_tier1_wind": {"tropical_cyclone_zone": 4}, "mexico_wind": {"tropical_cyclone_zone": 4},
    "japan_wind": {"tropical_cyclone_zone": 3}, "europe_wind": {"extratropical_storm_zone": 3},
    "california_eq": {"earthquake_zone": 4}, "japan_eq": {"earthquake_zone": 4},
    "mexico_eq": {"earthquake_zone": 3}, "pnw_eq": {"earthquake_zone": 3},
    "us_flood": {"river_flood_zone": 3}, "europe_flood": {"river_flood_zone": 3},
}


class MockNathan:
    """Hazard zones (0 = none ... 4 = highest) from the nearest gazetteer city. Global coverage."""

    name = "nathan"

    def __init__(self, options: dict | None = None):
        pass

    def lookup(self, latitude: float, longitude: float) -> dict[str, float] | None:
        scores = {"earthquake_zone": 1, "tropical_cyclone_zone": 0, "extratropical_storm_zone": 1, "river_flood_zone": 1}
        for zone in nearest_city(latitude, longitude).zones:
            scores.update(_NATHAN_BY_ZONE.get(zone, {}))
        return {k: float(v) for k, v in scores.items()}


class MockAthenium:
    """US-only severe convective storm and wildfire scores (0-10). None outside the US."""

    name = "athenium"

    def __init__(self, options: dict | None = None):
        pass

    def lookup(self, latitude: float, longitude: float) -> dict[str, float] | None:
        if nearest_city(latitude, longitude).country != "US":
            return None
        key = (round(latitude, 3), round(longitude, 3))
        return {
            "hail_score": round(_unit("hail", *key) * 10, 1),
            "wind_score": round(_unit("wind", *key) * 10, 1),
            "wildfire_score": round(_unit("fire", *key) * 10, 1),
        }


class MockSurveyStore:
    """Reads <mock_data_dir>/surveys/<insured>.json written by the synthetic generator."""

    def __init__(self, options: dict | None = None):
        self._dir = Path((options or {}).get("data_dir", "var/mock_data")) / "surveys"

    def get(self, insured: str, loc_number: str) -> SurveyRecord | None:
        path = self._dir / f"{slug(insured)}.json"
        if not path.exists():
            return None
        for raw in json.loads(path.read_text()):
            if str(raw["loc_number"]) == str(loc_number):
                return SurveyRecord.model_validate(raw)
        return None


class MockPriorYearStore:
    """Reads <mock_data_dir>/prior_year/<insured>.json written by the synthetic generator."""

    def __init__(self, options: dict | None = None):
        self._dir = Path((options or {}).get("data_dir", "var/mock_data")) / "prior_year"

    def get(self, insured: str) -> PriorYearSubmission | None:
        path = self._dir / f"{slug(insured)}.json"
        return PriorYearSubmission.model_validate_json(path.read_text()) if path.exists() else None
