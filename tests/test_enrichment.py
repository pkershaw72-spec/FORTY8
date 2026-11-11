"""Step 7 - Enrichment: survey, prior-year fill, building data, hazard, H3, year-on-year flags."""

import pytest

from forty8.agents.enrichment import EnrichmentAgent
from forty8.connectors.registry import build_connectors
from forty8.models.common import ResultStatus
from forty8.models.exposure import BuildingAttributes, GeocodeResult, Lineage, Location
from forty8.models.pipeline import GeocodedLocations, IntakeOutput
from forty8.models.results import PriorYearLocation, PriorYearSubmission, SurveyRecord
from helpers import make_ctx

INSURED = "Acme Synthetic Ltd"


def loc(number, resolution="rooftop", lat=25.7617, lon=-80.1918, **fields):
    base = dict(OccupancyCode=1102, ConstructionCode=5200, YearBuilt=2001, NumberOfStoreys=2, FloorArea=900.0,
                BuildingTIV=1_000_000.0, LocCurrency="USD")
    base.update(fields)
    return Location(
        LocNumber=str(number), lineage=Lineage(tab="USA", source_row=number + 4), Latitude=lat, Longitude=lon,
        geocode=GeocodeResult(resolution=resolution, provider="moodys_geocoder", confidence=0.9, latitude=lat, longitude=lon),
        **base)


class Stores:
    """Stub survey, prior-year and building-data connectors."""

    name = "building_data"

    def __init__(self, prior=None, surveys=None, building=None):
        self.prior = PriorYearSubmission(insured=INSURED, year=2026, locations=[
            PriorYearLocation(loc_number=str(n), attributes=a) for n, a in (prior or {}).items()]) if prior is not None else None
        self.surveys, self.building, self.building_calls = surveys or {}, building, 0

    def get(self, insured, loc_number=None):
        if loc_number is None:
            return self.prior
        attrs = self.surveys.get(str(loc_number))
        return SurveyRecord(loc_number=str(loc_number), survey_date="2026-06-15", attributes=attrs) if attrs else None

    def lookup(self, latitude, longitude):
        self.building_calls += 1
        return self.building


def run(settings, locations, stores=None, insured=INSURED, **kw):
    connectors = build_connectors(settings)
    stores = stores or Stores()
    connectors.prior_year_store = connectors.survey_store = connectors.building_data = stores
    inputs = {"geocoding": GeocodedLocations(locations=locations),
              "intake": IntakeOutput(sov_path="s", slip_path="s", insured=insured)}
    return EnrichmentAgent().run(make_ctx(settings, inputs=inputs, connectors=connectors, **kw)), stores


def test_every_value_records_its_source_and_hazard_is_attached(settings):
    result, _ = run(settings, [loc(1), loc(2, lat=35.6762, lon=139.6503)])
    assert result.status == ResultStatus.PASS
    miami, tokyo = result.output.locations
    assert miami.sources["BuildingTIV"].source == "sov:USA!row5" and miami.sources["YearBuilt"].source == "sov:USA!row5"
    assert miami.hazard["nathan.tropical_cyclone_zone"].value == 4.0 and miami.hazard["nathan.earthquake_zone"].source == "nathan"
    assert "athenium.hail_score" in miami.hazard and not any(k.startswith("athenium") for k in tokyo.hazard)  # US only
    assert tokyo.hazard["nathan.earthquake_zone"].value == 4.0
    assert len(miami.h3_cell) == 15 and miami.sources["h3_cell"].source == "h3:res8" and miami.h3_cell != tokyo.h3_cell
    assert result.output.completeness_after == 1.0
    assert result.audit[-1].rule_or_model == "rule:completeness_score"


def test_gap_filling_order_survey_then_prior_year_then_building_data(settings):
    gaps = dict(YearBuilt=None, NumberOfStoreys=None, FloorArea=None)
    stores = Stores(
        prior={1: {"YearBuilt": 1990, "NumberOfStoreys": 3, "BuildingTIV": 1_000_000.0, "LocCurrency": "USD"}},
        surveys={"1": {"YearBuilt": 1988, "sprinklered": "yes"}},
        building=BuildingAttributes(year_built=1970, number_of_storeys=9, floor_area_m2=1234.0),
    )
    result, _ = run(settings, [loc(1, **gaps)], stores)
    out, enriched = result.output, result.output.locations[0]
    assert (enriched.YearBuilt, enriched.sources["YearBuilt"].source) == (1988, "survey:2026-06-15")
    assert (enriched.NumberOfStoreys, enriched.sources["NumberOfStoreys"].source) == (3, "prior_year:2026")
    assert (enriched.FloorArea, enriched.sources["FloorArea"].source) == (1234.0, "building_data")
    assert enriched.sources["FloorArea"].confidence == 0.7
    assert enriched.extra["sprinklered"].value == "yes"
    assert out.filled_by_source == {"survey": 1, "prior_year": 1, "building_data": 1}
    assert out.completeness_before == pytest.approx(0.5) and out.completeness_after == 1.0
    assert any("missing this year, filled from prior year" in a.decision for a in result.audit)


def test_verified_survey_overrides_submitted_value_and_is_audited(settings):
    result, _ = run(settings, [loc(1, ConstructionCode=5050)], Stores(surveys={"1": {"ConstructionCode": 5150}}))
    enriched = result.output.locations[0]
    assert enriched.ConstructionCode == 5150 and enriched.sources["ConstructionCode"].source == "survey:2026-06-15"
    assert any("verified survey overrides submitted value 5050" in a.decision for a in result.audit)


def test_submitted_values_are_not_overwritten_by_prior_year_or_building_data(settings):
    stores = Stores(prior={1: {"YearBuilt": 1950, "BuildingTIV": 1_000_000.0}}, building=BuildingAttributes(year_built=1900))
    result, stores = run(settings, [loc(1)], stores)
    assert result.output.locations[0].YearBuilt == 2001 and stores.building_calls == 0


def test_building_data_needs_a_street_level_location_and_gaps_are_reported(settings):
    result, stores = run(settings, [loc(1, resolution="city", YearBuilt=None)],
                         Stores(building=BuildingAttributes(year_built=1970)))
    assert result.status == ResultStatus.PASS and stores.building_calls == 0
    assert result.output.locations[0].YearBuilt is None
    assert result.output.completeness_after == pytest.approx(5 / 6, abs=1e-4)
    assert result.output.completeness_by_field["YearBuilt"] == 0.0
    assert any("not at street level; gaps remain" in a.decision for a in result.audit)


@pytest.mark.parametrize("prior_tiv,flagged", [(800_000.0, False), (799_000.0, True), (1_340_000.0, True), (1_330_000.0, False)])
def test_year_on_year_threshold(settings, prior_tiv, flagged):
    result, _ = run(settings, [loc(1)], Stores(prior={1: {"BuildingTIV": prior_tiv, "LocCurrency": "USD"}}))
    assert (result.status == ResultStatus.EXCEPTION) == flagged
    assert len(result.output.yoy_flags) == int(flagged)


def test_large_change_is_flagged_then_confirmed_or_reverted(settings):
    stores = Stores(prior={1: {"BuildingTIV": 400_000.0, "LocCurrency": "USD"}, 9: {"BuildingTIV": 5.0}})
    flagged, _ = run(settings, [loc(1), loc(2)], stores)
    [item] = flagged.exceptions
    assert item.code == "LARGE_YOY_CHANGE" and item.subject == "yoy:1" and "+150%" in item.reason
    assert item.priority == 600_000 and item.learn.kind == "yoy_change"
    assert flagged.output.new_locations == ["2"] and flagged.output.dropped_locations == ["9"]

    confirmed, _ = run(settings, [loc(1)], stores, corrections={"yoy:1": "confirm"})
    assert confirmed.status == ResultStatus.PASS and confirmed.output.yoy_flags[0].resolution == "confirmed"
    assert confirmed.output.locations[0].BuildingTIV == 1_000_000

    reverted, _ = run(settings, [loc(1)], stores, corrections={"yoy:1": "use_prior"})
    assert reverted.status == ResultStatus.PASS and reverted.output.locations[0].BuildingTIV == 400_000
    assert reverted.output.locations[0].sources["BuildingTIV"].source == "prior_year:2026"

    invalid, _ = run(settings, [loc(1)], stores, corrections={"yoy:1": "maybe"})
    assert invalid.status == ResultStatus.EXCEPTION and "'maybe' is not a valid answer" in invalid.exceptions[0].reason

    learned, _ = run(settings, [loc(1)], stores, learned={(item.learn.kind, item.learn.key): "confirm"})
    assert learned.status == ResultStatus.PASS


def test_no_prior_year_or_no_insured_skips_comparison(settings):
    for kwargs in ({"stores": Stores()}, {"stores": Stores(prior={1: {"BuildingTIV": 1.0}}), "insured": None}):
        result, _ = run(settings, [loc(1)], **kwargs)
        assert result.status == ResultStatus.PASS and result.output.yoy_flags == []
        assert any("no prior-year submission found" in a.decision for a in result.audit)
