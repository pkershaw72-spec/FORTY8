"""Steps 1-7 and the slip lane running through the real orchestrator (steps 8-11 are placeholders)."""

from forty8.models.common import MappingStatus, RunStatus, StepStatus
from forty8.orchestrator.metrics import run_metrics
from forty8.synth.generator import write_unreadable_file
from helpers import answer

BUILT = ["intake", "cleaning", "contract", "schema_mapping", "coding", "ml_classify", "geocoding", "enrichment"]


def start(orc, sub, sov=None):
    return orc.start(sov or sub.sov_path, sub.slip_path, broker=sub.broker)


def work_queue(orc, run_id, sub, analyst="alice"):
    """Answer every open item from the synthetic truth and resume, until nothing is left."""
    codes = []
    for _ in range(10):
        items = orc.review.list(run_id)
        if not items:
            break
        for item in items:
            orc.review.correct(item.id, answer(item, sub), analyst)
            codes.append(item.code)
        orc.resume(run_id)
    return codes


def assert_coded_to_truth(orc, run_id, sub):
    truth = {loc.loc_number: loc for loc in sub.locations}
    locations = orc.output(run_id, "ml_classify").locations
    for loc in locations:
        expected = truth[loc.LocNumber]
        assert loc.OccupancyCode == expected.occupancy_code, (loc.LocNumber, loc.occupancy_text)
        assert loc.ConstructionCode == expected.construction_code, (loc.LocNumber, loc.construction_text)
        assert loc.BuildingTIV == expected.building and loc.LocCurrency == expected.currency
        assert loc.CountryCode == expected.country and loc.lineage.tab == expected.tab
    return locations


def assert_geocoded_and_enriched(orc, run_id, sub):
    truth = {loc.loc_number: loc for loc in sub.locations}
    geo, enriched = orc.output(run_id, "geocoding"), orc.output(run_id, "enrichment")
    assert geo.tiv_street_share >= 0.80
    by_class = {}
    for loc in geo.locations:
        by_class.setdefault(truth[loc.LocNumber].address_class, set()).add(loc.geocode.provider.split(":")[0])
    assert by_class["A"] == {"moodys_geocoder"} and by_class["B"] == {"bing_geocoder"}
    assert by_class["C"] == {"web_search"} and by_class["D"] == {"analyst", "moodys_geocoder"}
    assert enriched.completeness_after > enriched.completeness_before
    assert {"survey", "prior_year"} <= set(enriched.filled_by_source)
    assert [f.loc_number for f in enriched.yoy_flags] == [
        l.loc_number for l in sub.locations if "yoy_change" in l.defects]
    for loc in enriched.locations:
        if loc.Latitude is not None:
            assert loc.h3_cell and any(k.startswith("nathan.") for k in loc.hazard)
        for field in ("YearBuilt", "NumberOfStoreys"):
            if f"missing_{'year_built' if field == 'YearBuilt' else 'storeys'}" in truth[loc.LocNumber].defects:
                source = loc.sources.get(field)
                assert source is None or source.source.split(":")[0] in ("survey", "prior_year", "building_data")
                if source and source.source.split(":")[0] != "building_data":
                    expected = truth[loc.LocNumber]
                    assert getattr(loc, field) == (expected.year_built if field == "YearBuilt" else expected.storeys)


def test_clean_submission_runs_straight_through(make_orchestrator, clean_submission):
    orc = make_orchestrator(real=True)
    run_id = start(orc, clean_submission)
    assert orc.store.get_run(run_id)["status"] == RunStatus.COMPLETED
    metrics = run_metrics(orc.store, run_id)
    assert metrics.straight_through and metrics.analyst_touches == 0
    assert len(assert_coded_to_truth(orc, run_id, clean_submission)) == 24
    assert orc.output(run_id, "contract").overall_confidence >= 0.85
    geo, enriched = orc.output(run_id, "geocoding"), orc.output(run_id, "enrichment")
    assert geo.tiv_street_share == 1.0 and geo.by_provider == {"moodys_geocoder": 24}
    assert enriched.completeness_after == 1.0 and enriched.yoy_flags == []
    assert all(loc.h3_cell and loc.sources["BuildingTIV"].source.startswith("sov:") for loc in enriched.locations)
    methods = {e.rule_or_model for e in orc.audit.for_run(run_id) if e.agent in BUILT}
    assert not any(m.startswith(("ml:", "analyst:", "learned_mapping:")) for m in methods)  # rules were enough


def test_messy_submission_hits_each_exception_path_then_learns(make_orchestrator, messy_submission):
    orc = make_orchestrator(real=True)
    first = start(orc, messy_submission)
    assert orc.store.get_run(first)["status"] == RunStatus.AWAITING_REVIEW
    steps = orc.store.get_steps(first)
    assert steps["cleaning"]["status"] == steps["contract"]["status"] == StepStatus.AWAITING_REVIEW

    codes = work_queue(orc, first, messy_submission)
    assert set(codes) == {"AMBIGUOUS_HEADER_ROW", "LOW_CONFIDENCE_SLIP_TERM", "UNMAPPED_REQUIRED_COLUMN",
                          "LOW_CONFIDENCE_CLASSIFICATION", "LOW_TIV_GEOCODE", "LARGE_YOY_CHANGE"}
    assert codes.count("LOW_TIV_GEOCODE") == 1                    # only the largest unresolved site was needed
    assert orc.store.get_run(first)["status"] == RunStatus.COMPLETED
    locations = assert_coded_to_truth(orc, first, messy_submission)
    assert len(locations) == 25                                   # 24 locations plus the duplicated row
    assert {loc.lineage.tab for loc in locations} == {"USA", "Europe", "Japan", "México"}
    slip, truth = orc.output(first, "contract"), messy_submission.slip
    assert (slip.value("limit"), slip.value("deductible"), slip.value("inception")) == (
        truth.limit, truth.deductible, truth.inception)
    metrics = run_metrics(orc.store, first)
    assert not metrics.straight_through and metrics.analyst_touches == len(codes)
    sources = {s.source.split(":")[0] for loc in locations for s in loc.sources.values()}
    assert {"rule", "ml", "analyst"} <= sources
    assert_geocoded_and_enriched(orc, first, messy_submission)

    # Corrections are only proposed: the same submission still stops until a second analyst approves.
    proposed = orc.learning.list(MappingStatus.PROPOSED)
    assert len(proposed) == len(codes)
    second = start(orc, messy_submission)
    assert orc.store.get_run(second)["status"] == RunStatus.AWAITING_REVIEW

    for mapping in proposed:
        orc.learning.approve(mapping.id, analyst="bob")
    third = start(orc, messy_submission)
    assert orc.store.get_run(third)["status"] == RunStatus.COMPLETED
    assert run_metrics(orc.store, third).straight_through
    assert_coded_to_truth(orc, third, messy_submission)
    learned_by = {e.agent for e in orc.audit.for_run(third) if e.rule_or_model.startswith("learned_mapping:")}
    assert learned_by == {"cleaning", "contract", "schema_mapping", "coding", "geocoding", "enrichment"}


def test_learned_mappings_do_not_leak_when_kept_to_the_broker(make_orchestrator, messy_submission):
    orc = make_orchestrator(real=True)
    first = start(orc, messy_submission)
    work_queue(orc, first, messy_submission)
    for mapping in orc.learning.list(MappingStatus.PROPOSED):
        orc.learning.approve(mapping.id, analyst="bob", scope="broker")
    other = orc.start(messy_submission.sov_path, messy_submission.slip_path, broker="Another Broker")
    assert orc.store.get_run(other)["status"] == RunStatus.AWAITING_REVIEW
    same = start(orc, messy_submission)
    assert orc.store.get_run(same)["status"] == RunStatus.COMPLETED


def test_unreadable_sov_is_fixed_through_the_queue(make_orchestrator, clean_submission, tmp_path):
    orc = make_orchestrator(real=True)
    run_id = start(orc, clean_submission, sov=write_unreadable_file(tmp_path / "broken.xlsx"))
    steps = orc.store.get_steps(run_id)
    assert steps["intake"]["status"] == StepStatus.AWAITING_REVIEW
    assert steps["contract"]["status"] == StepStatus.PENDING       # slip lane waits for intake
    [item] = orc.review.list(run_id)
    assert item.code == "UNREADABLE_FILE"
    orc.review.correct(item.id, clean_submission.sov_path, "alice")
    assert orc.resume(run_id) == RunStatus.COMPLETED
