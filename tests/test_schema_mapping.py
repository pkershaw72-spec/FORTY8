"""Step 3 - Schema mapping."""

import pytest

from forty8.agents.schema_mapping import SchemaMappingAgent
from forty8.models.common import ResultStatus
from forty8.models.exposure import Lineage
from forty8.models.pipeline import CleanRow, CleanTable, TabSummary
from forty8.schema.target import load_schema
from forty8.synth.generator import HEADERS_DE, HEADERS_EN, HEADERS_ES
from helpers import make_ctx

ROW = [1, "Site", "1 Main St", "Miami", "FL", "33131", "USA", "Warehouse", "Steel Frame", 1999, 2, 10000.0,
       5e6, 2e6, 1e6, 8e6, "USD"]


def table(*tabs):
    out = CleanTable()
    for name, headers in tabs:
        out.tabs.append(TabSummary(tab=name, header_row=1, headers=list(headers)))
        out.rows.append(CleanRow(lineage=Lineage(tab=name, source_row=2), values=dict(zip(headers, ROW))))
    return out


def run(settings, clean, **kw):
    ctx = make_ctx(settings, inputs={"cleaning": clean}, **kw)
    return SchemaMappingAgent().run(ctx), ctx


def targets(result, tab):
    return {m.source_header: m.target_field for m in result.output.mappings if m.tab == tab}


def test_english_and_spanish_headers_map_by_rule_without_the_llm(settings):
    result, ctx = run(settings, table(("USA", HEADERS_EN), ("México", HEADERS_ES)))
    assert result.status == ResultStatus.PASS
    assert ctx.connectors.llm.calls == []
    assert all(m.method == "rule:header_synonyms" and m.target_field for m in result.output.mappings)
    usa, mx = result.output.rows
    assert usa.values["BuildingTIV"] == mx.values["BuildingTIV"] == 5e6
    assert mx.values["occupancy_text"] == "Warehouse" and mx.values["LocNumber"] == 1
    assert usa.values["FloorArea"] == pytest.approx(929.0, abs=0.1)   # 10,000 sq ft -> m2
    assert mx.values["FloorArea"] == 10000.0                           # already m2
    assert set(load_schema("oed").required) <= set(usa.values)


def test_other_languages_and_abbreviations_go_to_the_llm(settings):
    headers = [h if h != "Building Value" else "Bldg. Value" for h in HEADERS_EN]
    result, ctx = run(settings, table(("Europe", HEADERS_DE), ("USA", headers)))
    assert result.status == ResultStatus.PASS
    assert [task for task, _ in ctx.connectors.llm.calls] == ["header_mapping"]
    assert targets(result, "Europe")["Gebäudewert"] == "BuildingTIV"
    assert targets(result, "USA")["Bldg. Value"] == "BuildingTIV"
    german = next(m for m in result.output.mappings if m.source_header == "Baujahr")
    assert german.method == "llm:mock-llm" and german.confidence == 0.9


def test_unmapped_required_column_goes_to_review(settings):
    headers = [h if h != "Building Value" else "Amt (Note 3)" for h in HEADERS_EN]
    result, _ = run(settings, table(("Japan", headers), ("USA", HEADERS_EN)))
    assert result.status == ResultStatus.EXCEPTION
    [item] = result.exceptions
    assert item.code == "UNMAPPED_REQUIRED_COLUMN" and item.subject == "header:Amt (Note 3)"
    assert item.payload["missing_required"] == ["BuildingTIV"] and item.payload["samples"] == [5e6]
    assert (item.learn.kind, item.learn.key) == ("header", "Amt (Note 3)")

    fixed, _ = run(settings, table(("Japan", headers)), corrections={"header:Amt (Note 3)": "BuildingTIV"})
    assert fixed.status == ResultStatus.PASS and fixed.output.rows[0].values["BuildingTIV"] == 5e6
    assert any(a.rule_or_model == "analyst:alice" for a in fixed.audit)

    learned, _ = run(settings, table(("Japan", headers)), learned={("header", "Amt (Note 3)"): "BuildingTIV"})
    assert learned.status == ResultStatus.PASS
    assert any(a.rule_or_model == "learned_mapping:99" for a in learned.audit)


def test_ignoring_the_only_candidate_reports_the_column_as_absent(settings):
    headers = [h if h != "Building Value" else "Amt (Note 3)" for h in HEADERS_EN]
    result, _ = run(settings, table(("Japan", headers)), corrections={"header:Amt (Note 3)": "ignore"})
    assert [e.code for e in result.exceptions] == ["REQUIRED_COLUMN_ABSENT"]


def test_unmapped_optional_header_is_ignored_and_audited(settings):
    result, _ = run(settings, table(("USA", [*HEADERS_EN, "Broker comments"])))
    assert result.status == ResultStatus.PASS
    assert targets(result, "USA")["Broker comments"] is None
    assert any(a.input_ref.endswith("Broker comments") and "not mapped" in a.decision for a in result.audit)


def test_llm_mapping_below_threshold_is_not_accepted(settings):
    settings.thresholds.header_min_confidence = 0.95
    result, _ = run(settings, table(("Europe", HEADERS_DE)))   # mock LLM answers at 0.90
    assert result.status == ResultStatus.EXCEPTION
    assert {e.code for e in result.exceptions} == {"UNMAPPED_REQUIRED_COLUMN"}
