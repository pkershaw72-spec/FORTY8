"""Step 2 - Cleaning."""

from forty8.agents.cleaning import CleaningAgent
from forty8.models.common import ResultStatus
from forty8.models.pipeline import IntakeOutput, RawSheet
from helpers import make_ctx

HEAD = ["Loc #", "Street Address", "City", "Total Insured Value"]
ALT = ["Standort Nr.", "Adresse", "Stadt", "Gesamtversicherungssumme"]
MESSY = [
    ["Acme Synthetic Ltd"], ["Schedule of Values 2027"], [],
    HEAD,
    [1, "1 Main St", "Miami", 100.0],
    [2, "2 Main St", "Miami", 200.0],
    ["Subtotal FL", None, None, 300.0],
    [],
    [3, "3 Oak Ave", "Dallas", 50.0],
    ["Subtotal TX", None, None, 50.0],
    ["TOTAL", None, None, 350.0],
]
NOTES = [["Notes to the schedule"], ["1. Values are replacement cost."]]


def run(settings, sheets, **kw):
    intake = IntakeOutput(sov_path="s.xlsx", slip_path="s.docx", sheets=[RawSheet(name=n, rows=r) for n, r in sheets])
    return CleaningAgent().run(make_ctx(settings, inputs={"intake": intake}, **kw))


def test_removes_title_subtotal_and_blank_rows_and_keeps_lineage(settings):
    result = run(settings, [("USA", MESSY)])
    assert result.status == ResultStatus.PASS
    out = result.output
    assert [(r.lineage.tab, r.lineage.source_row) for r in out.rows] == [("USA", 5), ("USA", 6), ("USA", 9)]
    assert out.rows[0].values == {"Loc #": 1, "Street Address": "1 Main St", "City": "Miami", "Total Insured Value": 100.0}
    tab = out.tabs[0]
    assert (tab.header_row, tab.title_rows_removed, tab.subtotal_rows_removed, tab.blank_rows_removed) == (4, 2, 3, 1)
    assert [(t.kind, t.values["Total Insured Value"]) for t in out.stated_totals] == [
        ("subtotal", 300.0), ("subtotal", 50.0), ("total", 350.0)]
    assert any(a.rule_or_model == "rule:header_detection" and a.confidence == 0.95 for a in result.audit)


def test_combines_tabs_and_skips_non_data_tabs(settings):
    second = [["No. Ubic.", "Dirección", "Ciudad", "Suma Asegurada Total"], [7, "Av. Uno 1", "Monterrey", 10.0]]
    out = run(settings, [("USA", MESSY), ("México", second), ("Notes", NOTES)]).output
    assert [r.lineage.tab for r in out.rows] == ["USA", "USA", "USA", "México"]
    assert out.rows[3].values["Ciudad"] == "Monterrey"
    notes = out.tabs[2]
    assert notes.skipped and notes.skip_reason == "no tabular data"


def test_ambiguous_header_goes_to_review_then_correction_applies(settings):
    rows = [["Title"], ALT, HEAD, [1, "1 Hafenstrasse", "Hamburg", 100.0]]
    result = run(settings, [("Europe", rows), ("USA", MESSY)])
    assert result.status == ResultStatus.EXCEPTION
    [item] = result.exceptions
    assert item.code == "AMBIGUOUS_HEADER_ROW" and item.subject == "header_row:Europe"
    assert set(item.payload["candidates"]) == {"2", "3"} and "rows 2, 3" in item.reason
    assert len(result.output.rows) == 3  # the unambiguous tab is still cleaned

    fixed = run(settings, [("Europe", rows)], corrections={"header_row:Europe": 3})
    assert fixed.status == ResultStatus.PASS
    assert fixed.output.rows[0].values["City"] == "Hamburg" and len(fixed.output.rows) == 1

    german = run(settings, [("Europe", rows)], corrections={"header_row:Europe": 2})
    assert german.output.rows[0].values["Stadt"] == "Hamburg" and len(german.output.rows) == 1  # English row dropped

    invalid = run(settings, [("Europe", rows)], corrections={"header_row:Europe": "the second one"})
    assert invalid.status == ResultStatus.EXCEPTION


def test_learned_header_choice_is_reused(settings):
    rows = [["Title"], ALT, HEAD, [1, "1 Hafenstrasse", "Hamburg", 100.0]]
    [item] = run(settings, [("Europe", rows)]).exceptions
    learned = {(item.learn.kind, item.learn.key): item.learn.values["3"]}
    shifted = [["Another title"], ["More"], *rows[1:]]   # same headers on different row numbers
    result = run(settings, [("Europe", shifted)], learned=learned)
    assert result.status == ResultStatus.PASS and result.output.tabs[0].header_row == 4
    assert any(a.rule_or_model == "learned_mapping:99" for a in result.audit)


def test_data_without_header_goes_to_review(settings):
    result = run(settings, [("Sheet1", [[1, "1 Main St", "Miami", 100.0], [2, "2 Main St", "Miami", 5.0]])])
    assert result.exceptions[0].code == "AMBIGUOUS_HEADER_ROW" and "no recognisable header" in result.exceptions[0].reason
