"""Step 8 - Audit gate: each data quality test, its fix, and the re-test."""

from datetime import date

import pytest

from forty8.agents.audit_gate import AuditGateAgent
from forty8.models.common import ResultStatus
from forty8.models.exposure import GeocodeResult, Lineage, Location
from forty8.models.pipeline import CleanTable, ColumnMapping, EnrichedLocations, MappedTable, StatedTotal
from helpers import make_ctx

TIV_HEADER = "Total Insured Value"


def loc(number, tab="USA", resolution="rooftop", street=None, **fields):
    base = dict(ConstructionCode=5200, YearBuilt=2001, NumberOfStoreys=2, BuildingTIV=100.0, LocCurrency="USD")
    base.update(fields)
    return Location(LocNumber=str(number), StreetAddress=street or f"{number} Main St", City="Miami",
                    lineage=Lineage(tab=tab, source_row=int(number) + 4),
                    geocode=GeocodeResult(resolution=resolution, provider="moodys_geocoder", confidence=0.9), **base)


def run(settings, locations, totals=(), header_target="stated_tiv", **kw):
    clean = CleanTable(stated_totals=[
        StatedTotal(tab=tab, kind=kind, label=kind, source_row=99, values={TIV_HEADER: value})
        for tab, kind, value in totals])
    mapped = MappedTable(target_schema="oed", mappings=[
        ColumnMapping(tab=tab, source_header=TIV_HEADER, target_field=header_target, confidence=1.0, method="rule")
        for tab in {t[0] for t in totals}])
    inputs = {"enrichment": EnrichedLocations(locations=locations), "cleaning": clean, "schema_mapping": mapped}
    return AuditGateAgent().run(make_ctx(settings, inputs=inputs, **kw))


def codes(result):
    return [e.code for e in result.exceptions]


def test_clean_data_passes_every_test(settings):
    result = run(settings, [loc(1, stated_tiv=100.0), loc(2, stated_tiv=100.0)], totals=[("USA", "total", 200.0)])
    assert result.status == ResultStatus.PASS
    assert [t.name for t in result.output.tests] == [
        "no_duplicates", "year_built_in_range", "construction_vs_storeys", "tiv_row_totals",
        "tiv_reconciles_to_sov", "tiv_at_street_level"]
    assert all(t.passed and t.failures == 0 for t in result.output.tests)
    assert sum(a.rule_or_model.startswith("test:") and a.decision.startswith("PASS") for a in result.audit) == 6


def test_duplicates_by_location_number(settings):
    locations = [loc(1), loc(1), loc(2)]
    result = run(settings, locations)
    assert codes(result) == ["AUDIT_DUPLICATE"] and result.exceptions[0].subject == "duplicate:1"
    assert "appears 2 times (USA row 5; USA row 5)" in result.exceptions[0].reason

    removed = run(settings, locations, corrections={"duplicate:1": "remove"})
    assert removed.status == ResultStatus.PASS and [l.LocNumber for l in removed.output.locations] == ["1", "2"]
    kept = run(settings, locations, corrections={"duplicate:1": "keep"})
    assert kept.status == ResultStatus.PASS and len(kept.output.locations) == 3
    assert kept.output.tests[0].waived == 1


def test_duplicates_by_address_and_value_under_different_numbers(settings):
    result = run(settings, [loc(1, street="5 Dock Rd"), loc(2, street="5 dock rd"), loc(3, street="5 Dock Rd", BuildingTIV=7.0)])
    assert codes(result) == ["AUDIT_DUPLICATE"] and result.exceptions[0].subject == "duplicate:1+2"


def test_year_built_range_and_fix(settings):
    this_year = date.today().year
    ok = run(settings, [loc(1, YearBuilt=1700), loc(2, YearBuilt=this_year + 1), loc(3, YearBuilt=None)])
    assert ok.status == ResultStatus.PASS
    bad = run(settings, [loc(1, YearBuilt=1699), loc(2, YearBuilt=this_year + 2), loc(3, YearBuilt=1066)])
    assert codes(bad) == ["AUDIT_YEAR_BUILT"] * 3 and bad.output.tests[1].failures == 3

    fixed = run(settings, [loc(3, YearBuilt=1066)], corrections={"year_built:3": 1998})
    assert fixed.status == ResultStatus.PASS
    assert fixed.output.locations[0].YearBuilt == 1998 and fixed.output.locations[0].sources["YearBuilt"].source == "analyst:alice"
    cleared = run(settings, [loc(3, YearBuilt=1066)], corrections={"year_built:3": "clear"})
    assert cleared.status == ResultStatus.PASS and cleared.output.locations[0].YearBuilt is None
    invalid = run(settings, [loc(3, YearBuilt=1066)], corrections={"year_built:3": 3000})
    assert codes(invalid) == ["AUDIT_YEAR_BUILT"] and "'3000' is not a year between" in invalid.exceptions[0].reason


def test_construction_vs_storeys_and_fix(settings):
    ok = run(settings, [loc(1, ConstructionCode=5050, NumberOfStoreys=6), loc(2, ConstructionCode=5150, NumberOfStoreys=60),
                        loc(3, ConstructionCode=5050, NumberOfStoreys=None)])
    assert ok.status == ResultStatus.PASS
    tall_wood = [loc(1, ConstructionCode=5050, NumberOfStoreys=25)]
    bad = run(settings, tall_wood)
    assert codes(bad) == ["AUDIT_CONSTRUCTION_STOREYS"] and "maximum 6" in bad.exceptions[0].reason

    for fix, expected in (({"NumberOfStoreys": 2}, (5050, 2)), ({"ConstructionCode": 5150}, (5150, 25))):
        fixed = run(settings, tall_wood, corrections={"plausibility:1": fix})
        assert fixed.status == ResultStatus.PASS
        assert (fixed.output.locations[0].ConstructionCode, fixed.output.locations[0].NumberOfStoreys) == expected
    still_bad = run(settings, tall_wood, corrections={"plausibility:1": {"NumberOfStoreys": 9}})
    assert codes(still_bad) == ["AUDIT_CONSTRUCTION_STOREYS"]
    accepted = run(settings, tall_wood, corrections={"plausibility:1": "accept"})
    assert accepted.status == ResultStatus.PASS and accepted.output.locations[0].NumberOfStoreys == 25


def test_row_total_must_match_components(settings):
    bad = run(settings, [loc(1, stated_tiv=150.0)])
    assert codes(bad) == ["AUDIT_TIV_ROW"] and "says 150 but building + contents + BI is 100" in bad.exceptions[0].reason
    assert run(settings, [loc(1, stated_tiv=150.0)], corrections={"tiv_row:1": "accept"}).status == ResultStatus.PASS


@pytest.mark.parametrize("stated,passes", [(200.0, True), (200.19, True), (200.21, False), (5_000_200.0, False)])
def test_tiv_reconciles_to_stated_tab_total_within_tolerance(settings, stated, passes):
    result = run(settings, [loc(1), loc(2), loc(3, tab="Europe")], totals=[("USA", "total", stated)])
    assert (result.status == ResultStatus.PASS) == passes
    if not passes:
        [item] = result.exceptions
        assert item.code == "AUDIT_TIV_RECONCILE" and item.subject == "tiv_reconcile:USA"
        assert item.payload["pipeline_total"] == 200.0 and item.priority == pytest.approx(stated - 200.0)
        accepted = run(settings, [loc(1), loc(2)], totals=[("USA", "total", stated)], corrections={"tiv_reconcile:USA": "accept"})
        assert accepted.status == ResultStatus.PASS and accepted.output.tests[4].waived == 1


def test_reconciliation_falls_back_to_components_and_subtotals(settings):
    components = run(settings, [loc(1)], totals=[("USA", "total", 100.0)], header_target="BuildingTIV")
    assert components.status == ResultStatus.PASS and "1 tab total(s) reconcile" in components.output.tests[4].detail
    subtotals = run(settings, [loc(1), loc(2)], totals=[("USA", "subtotal", 100.0), ("USA", "subtotal", 100.0)])
    assert subtotals.status == ResultStatus.PASS
    assert codes(run(settings, [loc(1), loc(2)], totals=[("USA", "subtotal", 100.0)])) == ["AUDIT_TIV_RECONCILE"]


def test_street_level_share_is_retested(settings):
    locations = [loc(1), loc(2, resolution="city", BuildingTIV=30.0)]
    bad = run(settings, locations)
    assert codes(bad) == ["AUDIT_GEOCODE_TIV"] and "76.9%" in bad.exceptions[0].reason
    assert run(settings, locations, corrections={"geocode_tiv": "accept"}).status == ResultStatus.PASS
    assert run(settings, [loc(1), loc(2, resolution="city", BuildingTIV=25.0)]).status == ResultStatus.PASS


def test_every_test_is_rerun_after_a_fix(settings):
    locations = [loc(1, YearBuilt=1066), loc(2, ConstructionCode=5050, NumberOfStoreys=25)]
    assert set(codes(run(settings, locations))) == {"AUDIT_YEAR_BUILT", "AUDIT_CONSTRUCTION_STOREYS"}
    partly = run(settings, locations, corrections={"year_built:1": 1998})
    assert codes(partly) == ["AUDIT_CONSTRUCTION_STOREYS"] and partly.output.tests[1].passed
