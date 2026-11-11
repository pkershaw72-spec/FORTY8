"""Step 1 - Intake."""

from forty8.agents.intake import IntakeAgent, _csv_cell
from forty8.models.common import ResultStatus
from forty8.synth.generator import Defects, generate, write_unreadable_file
from helpers import make_ctx


def run(settings, sub=None, **kw):
    if sub is not None:
        kw.setdefault("sov", sub.sov_path), kw.setdefault("slip", sub.slip_path)
    return IntakeAgent().run(make_ctx(settings, **kw))


def test_reads_xlsx_and_docx(settings, clean_submission):
    result = run(settings, clean_submission)
    assert result.status == ResultStatus.PASS
    out = result.output
    assert [s.name for s in out.sheets] == ["SoV"] and len(out.sheets[0].rows) == 25
    assert "Limit of Liability" in out.slip_text
    assert out.insured == clean_submission.insured
    assert out.languages == ["en"]
    assert all(a.rule_or_model and 0 <= a.confidence <= 1 for a in result.audit)


def test_reads_all_tabs_and_detects_spanish(settings, messy_submission):
    out = run(settings, messy_submission).output
    assert [s.name for s in out.sheets] == ["USA", "Europe", "Japan", "México", "Notes"]
    assert out.sheet_languages["México"] == "es" and out.sheet_languages["USA"] == "en"
    assert out.languages == ["en", "es"] or set(out.languages) >= {"en", "es"}


def test_reads_csv_and_pdf(settings, tmp_path):
    sub = generate(tmp_path, profile="clean", defects=Defects(title_rows=True), sov_format="csv", slip_format="pdf")
    out = run(settings, sub).output
    rows = out.sheets[0].rows
    assert rows[3][0] == "Loc #" and rows[4][0] == 1          # numbers parsed
    assert _csv_cell("06600") == "06600" and _csv_cell("1250000.50") == 1250000.5  # leading zeros stay text
    assert out.insured == sub.insured and "Deductible" in out.slip_text


def test_insured_override_wins(settings, clean_submission):
    assert run(settings, clean_submission, insured="Override Ltd").output.insured == "Override Ltd"


def test_unreadable_files_go_to_review(settings, tmp_path, clean_submission):
    bad = write_unreadable_file(tmp_path / "broken.xlsx")
    result = run(settings, sov=bad, slip=tmp_path / "missing.docx")
    assert result.status == ResultStatus.EXCEPTION
    assert {(e.code, e.subject) for e in result.exceptions} == {
        ("UNREADABLE_FILE", "file:sov"), ("UNREADABLE_FILE", "file:slip")}
    assert "unreadable" in result.exceptions[0].reason

    unsupported = run(settings, sov=tmp_path / "sov.xls", slip=clean_submission.slip_path)
    assert "unsupported SoV format" in unsupported.exceptions[0].reason

    fixed = run(settings, sov=bad, slip=clean_submission.slip_path, corrections={"file:sov": clean_submission.sov_path})
    assert fixed.status == ResultStatus.PASS and fixed.output.sov_path == clean_submission.sov_path
    assert any(a.rule_or_model == "analyst:alice" for a in fixed.audit)
