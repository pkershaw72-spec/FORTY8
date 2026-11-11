"""Steps 4 and 5 - Coding and ML classification."""

import pytest

from forty8.agents.coding import CodingAgent
from forty8.agents.ml_classify import MLClassifyAgent
from forty8.ml.classifier import get_classifier
from forty8.models.common import ResultStatus
from forty8.models.exposure import Lineage
from forty8.models.pipeline import MappedRow, MappedTable
from forty8.reference.coding_rules import rule_lookup
from forty8.synth.generator import (
    CONSTRUCTION_TEXT, OCCUPANCY_TEXT, UNKNOWN_CONSTRUCTION, UNKNOWN_OCCUPANCY,
)
from helpers import make_ctx


def mapped(*pairs):
    rows = [MappedRow(lineage=Lineage(tab="T", source_row=i + 2), values={
        "LocNumber": float(i + 1), "StreetAddress": "1 Main St", "City": "Monterrey", "CountryCode": "México",
        "PostalCode": 64000, "YearBuilt": "1999", "BuildingTIV": "1,000,000", "ContentsTIV": 500000 * (i + 1),
        "LocCurrency": "mxn", "occupancy_text": occ, "construction_text": con,
    }) for i, (occ, con) in enumerate(pairs)]
    return MappedTable(target_schema="oed", rows=rows)


def code(settings, table, **kw):
    return CodingAgent().run(make_ctx(settings, inputs={"schema_mapping": table}, **kw))


def classify(settings, table, **kw):
    coded = code(settings, table).output
    ctx = make_ctx(settings, inputs={"coding": coded}, **kw)
    return MLClassifyAgent().run(ctx), ctx


# ---- step 4
def test_rules_code_english_and_spanish_and_coerce_types(settings):
    result = code(settings, mapped(("Warehouse", "Steel Frame"), ("Bodega", "Concreto Reforzado")))
    assert result.status == ResultStatus.PASS and result.output.pending == []
    a, b = result.output.locations
    assert (a.OccupancyCode, a.ConstructionCode) == (1102, 5200)
    assert (b.OccupancyCode, b.ConstructionCode) == (1102, 5150)
    assert a.sources["OccupancyCode"].source == "rule:occupancy_lookup"
    assert (a.LocNumber, a.CountryCode, a.PostalCode, a.YearBuilt, a.LocCurrency) == ("1", "MX", "64000", 1999, "MXN")
    assert a.BuildingTIV == 1_000_000 and b.tiv == 2_000_000 and a.lineage.source_row == 2


def test_no_rule_match_is_passed_to_step_5_not_to_review(settings):
    result = code(settings, mapped(("Server farm", "Timber"), ("Server farm", "Masonry"), (None, None)))
    assert result.status == ResultStatus.PASS and result.exceptions == []
    assert [(p.field, p.text, p.row_indexes) for p in result.output.pending] == [
        ("occupancy", "Server farm", [0, 1]), ("construction", "Timber", [0])]
    assert result.output.locations[2].OccupancyCode is None
    assert any("passed to ML" in a.decision for a in result.audit)
    assert any("left for enrichment" in a.decision for a in result.audit)


def test_approved_learned_mapping_is_used_before_rules(settings):
    result = code(settings, mapped(("Misc ops", "Type B")),
                  learned={("occupancy", "Misc ops"): "warehouse", ("construction", "Type B"): 5100})
    loc = result.output.locations[0]
    assert (loc.OccupancyCode, loc.ConstructionCode) == (1102, 5100) and result.output.pending == []
    assert loc.sources["ConstructionCode"].source == "learned_mapping:99"


# ---- step 5
def test_confident_classification_is_applied(settings):
    result, ctx = classify(settings, mapped(("Server farm", "Structural steel")))
    assert result.status == ResultStatus.PASS
    loc = result.output.locations[0]
    assert (loc.OccupancyCode, loc.ConstructionCode) == (1156, 5200)
    assert loc.sources["OccupancyCode"].source == "ml:tfidf_logreg_v1"
    assert loc.sources["OccupancyCode"].confidence >= settings.thresholds.ml_min_confidence
    assert ctx.connectors.llm.calls == []


def test_llm_second_opinion_when_classifier_is_unsure(settings):
    text = "DC / cross-dock facility"
    assert get_classifier("occupancy").predict(text)[0][1] < settings.thresholds.ml_min_confidence
    result, ctx = classify(settings, mapped((text, "Masonry")))
    assert result.status == ResultStatus.PASS
    assert result.output.locations[0].OccupancyCode == 1102
    assert result.output.classifications[0].method == "llm:mock-llm"
    assert [task for task, _ in ctx.connectors.llm.calls] == ["free_text_coding"]


def test_below_threshold_goes_to_review_prioritised_by_tiv(settings):
    table = mapped((UNKNOWN_OCCUPANCY, UNKNOWN_CONSTRUCTION), ("Warehouse", UNKNOWN_CONSTRUCTION))
    result, _ = classify(settings, table)
    assert result.status == ResultStatus.EXCEPTION
    by_subject = {e.subject: e for e in result.exceptions}
    occ, con = by_subject[f"occupancy:{UNKNOWN_OCCUPANCY}"], by_subject[f"construction:{UNKNOWN_CONSTRUCTION}"]
    assert occ.code == con.code == "LOW_CONFIDENCE_CLASSIFICATION"
    assert con.payload["locations"] == ["1", "2"] and con.priority == 3_500_000 and occ.priority == 1_500_000
    assert (con.learn.kind, con.learn.key) == ("construction", UNKNOWN_CONSTRUCTION)
    assert len(con.payload["suggestions"]) == 3
    assert result.output.locations[0].OccupancyCode is None


def test_analyst_correction_by_class_name_or_code(settings):
    table = mapped((UNKNOWN_OCCUPANCY, UNKNOWN_CONSTRUCTION))
    result, _ = classify(settings, table, corrections={
        f"occupancy:{UNKNOWN_OCCUPANCY}": "light_manufacturing", f"construction:{UNKNOWN_CONSTRUCTION}": 5100})
    assert result.status == ResultStatus.PASS
    loc = result.output.locations[0]
    assert (loc.OccupancyCode, loc.ConstructionCode) == (1152, 5100)
    assert loc.sources["OccupancyCode"].source == "analyst:alice"

    bad, _ = classify(settings, table, corrections={f"occupancy:{UNKNOWN_OCCUPANCY}": "spaceship"})
    assert any("'spaceship' is not a valid occupancy" in e.reason for e in bad.exceptions)


def test_threshold_boundary(settings):
    confidence = get_classifier("occupancy").predict("Server farm")[0][1]
    settings.thresholds.ml_min_confidence = confidence
    assert classify(settings, mapped(("Server farm", "Masonry")))[0].status == ResultStatus.PASS
    settings.thresholds.ml_min_confidence = confidence + 1e-6
    assert classify(settings, mapped(("Server farm", "Masonry")))[0].status == ResultStatus.EXCEPTION


@pytest.mark.parametrize("field,table,unknown", [
    ("occupancy", OCCUPANCY_TEXT, UNKNOWN_OCCUPANCY), ("construction", CONSTRUCTION_TEXT, UNKNOWN_CONSTRUCTION)])
def test_rules_and_classifier_against_the_synthetic_vocabulary(settings, field, table, unknown):
    """Canonical wording is coded by rule; variants are never confidently wrong; junk is never confident."""
    threshold, clf = settings.thresholds.ml_min_confidence, get_classifier(field)
    confident = 0
    for label, (en, en_variants, es, es_variants) in table.items():
        assert rule_lookup(field, en) == label and rule_lookup(field, es) == label
        for variant in en_variants + es_variants:
            assert rule_lookup(field, variant) is None
            predicted, confidence = clf.predict(variant)[0]
            if confidence >= threshold:
                confident += 1
                assert predicted == label, variant
    assert confident >= 10
    for junk in (unknown, "see attached", "various", "n/a", "TBC"):
        assert clf.predict(junk)[0][1] < threshold
