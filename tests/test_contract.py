"""Step 2b - Contract (slip lane): term coding, the 85% gate, corrections and re-scoring."""

import pytest

from forty8.agents.contract import ContractAgent
from forty8.connectors.registry import build_connectors
from forty8.models.common import ResultStatus
from forty8.models.pipeline import IntakeOutput
from forty8.models.results import LLMResponse
from forty8.synth.generator import Defects, generate, slip_lines
from helpers import answer, make_ctx

GOOD = {"insured": "Acme", "inception": "2027-01-01", "expiry": "2027-12-31", "currency": "USD",
        "limit": 1e8, "deductible": 2.5e5, "perils": ["windstorm"]}


class StubLLM:
    """Returns fixed terms so the gate can be tested at exact confidences."""

    model = "stub"

    def __init__(self, confidences: dict, values: dict | None = None):
        self.terms = [{"name": n, "value": (values or GOOD)[n], "confidence": c, "source_text": f"{n} line"}
                      for n, c in confidences.items()]

    def complete(self, task, instructions, content, schema=None):
        return LLMResponse(data={"terms": self.terms}, model=self.model)


def run_text(settings, text, **kw):
    intake = IntakeOutput(sov_path="s.xlsx", slip_path="s.docx", slip_text=text)
    return ContractAgent().run(make_ctx(settings, inputs={"intake": intake}, **kw))


def run_stub(settings, confidences, values=None, **kw):
    connectors = build_connectors(settings)
    connectors.llm = StubLLM(confidences, values)
    return run_text(settings, "slip", connectors=connectors, **kw)


def text_of(submission):
    return "\n".join(slip_lines(submission.slip, submission.defects))


def test_clean_slip_is_coded_and_passes_the_gate(settings, clean_submission):
    result = run_text(settings, text_of(clean_submission))
    assert result.status == ResultStatus.PASS
    slip, truth = result.output, clean_submission.slip
    assert slip.overall_confidence >= 0.85 and slip.flagged == []
    assert slip.overall_confidence == min(t.confidence for t in slip.terms)
    for name in ("insured", "inception", "expiry", "currency", "limit", "deductible", "perils", "umr"):
        assert slip.value(name) == getattr(truth, name), name
    assert slip.value("total_tiv") == pytest.approx(truth.total_tiv_usd, abs=1)
    coded = {(t.value["peril"], t.value["limit"]) for t in slip.sublimits}
    assert coded == {(s["peril"], s["limit"]) for s in truth.sublimits}
    assert slip.get("sublimit:earthquake:california").value["zone_text"] == "California"
    assert all(a.rule_or_model and 0 <= a.confidence <= 1 for a in result.audit)
    assert result.audit[-1].rule_or_model == "rule:slip_gate_min_term_confidence"


@pytest.mark.parametrize("confidence,expected", [(0.85, ResultStatus.PASS), (0.849, ResultStatus.EXCEPTION)])
def test_gate_is_the_minimum_term_confidence_at_85_percent(settings, confidence, expected):
    result = run_stub(settings, {**{n: 0.99 for n in GOOD}, "limit": confidence})
    assert result.status == expected
    assert result.output.overall_confidence == confidence
    assert [e.subject for e in result.exceptions] == ([] if expected == ResultStatus.PASS else ["term:limit"])


def test_gate_threshold_comes_from_config(settings):
    settings.thresholds.slip_min_confidence = 0.60
    assert run_stub(settings, {**{n: 0.99 for n in GOOD}, "limit": 0.61}).status == ResultStatus.PASS


def test_ambiguous_slip_flags_only_the_hedged_terms(settings, messy_submission):
    result = run_text(settings, text_of(messy_submission))
    assert result.status == ResultStatus.EXCEPTION
    assert set(result.output.flagged) == {"inception", "expiry", "limit", "deductible", "perils"}
    assert result.output.overall_confidence < 0.85
    limit = next(e for e in result.exceptions if e.subject == "term:limit")
    assert limit.code == "LOW_CONFIDENCE_SLIP_TERM" and "as may be agreed" in limit.payload["source_text"]
    assert limit.learn.kind == "slip_term" and limit.learn.key.startswith("limit|Limit of Liability")
    assert result.output.get("currency").confidence >= 0.85   # unhedged terms are untouched


def test_corrections_are_applied_and_the_slip_is_rescored(settings, messy_submission):
    text = text_of(messy_submission)
    first = run_text(settings, text)
    truth = messy_submission.slip
    fixes = {"term:limit": truth.limit, "term:deductible": "USD 250k", "term:perils": "windstorm, earthquake, flood"}
    partial = run_text(settings, text, corrections=fixes)
    assert partial.status == ResultStatus.EXCEPTION                       # dates still flagged
    assert set(partial.output.flagged) == {"inception", "expiry"}
    assert partial.output.get("limit").confidence == 1.0 and partial.output.value("deductible") == 250_000

    fixes |= {"term:inception": truth.inception, "term:expiry": truth.expiry}
    done = run_text(settings, text, corrections=fixes)
    assert done.status == ResultStatus.PASS and done.output.overall_confidence >= 0.85
    assert done.output.get("limit").method == "analyst:alice"
    assert done.output.overall_confidence > first.output.overall_confidence


def test_invalid_correction_keeps_the_term_flagged(settings):
    confidences = {**{n: 0.99 for n in GOOD}, "limit": 0.4}
    result = run_stub(settings, confidences, corrections={"term:limit": "plenty"})
    assert result.status == ResultStatus.EXCEPTION
    assert "'plenty' is not a valid value for limit" in result.exceptions[0].reason


def test_missing_required_term_is_flagged_and_not_learnable(settings):
    result = run_stub(settings, {n: 0.99 for n in GOOD if n != "deductible"})
    [item] = result.exceptions
    assert item.subject == "term:deductible" and "was not found" in item.reason and item.learn is None
    assert result.output.overall_confidence == 0.0


def test_expiry_must_follow_inception(settings):
    result = run_stub(settings, {n: 0.99 for n in GOOD}, values={**GOOD, "expiry": "2026-12-31"})
    assert {e.subject for e in result.exceptions} == {"term:inception", "term:expiry"}
    assert "Expiry is not after inception" in result.exceptions[0].reason


def test_learned_slip_wording_is_reused(settings, messy_submission):
    text = text_of(messy_submission)
    learned = {(e.learn.kind, e.learn.key): answer_for(e, messy_submission) for e in run_text(settings, text).exceptions}
    result = run_text(settings, text, learned=learned)
    assert result.status == ResultStatus.PASS
    assert result.output.get("perils").method == "learned_mapping:99"


def answer_for(exception, submission):
    return getattr(submission.slip, exception.payload["term"])


def test_pdf_slip_with_wrapped_lines_reads_the_same(settings, tmp_path):
    from forty8.agents.intake import read_slip

    sub = generate(tmp_path, profile="messy", defects=Defects(ambiguous_slip=True), slip_format="pdf")
    pdf = run_text(settings, read_slip(tmp_path / "slip_messy.pdf"))
    plain = run_text(settings, text_of(sub))
    assert {t.name: t.value for t in pdf.output.terms} == {t.name: t.value for t in plain.output.terms}
    assert pdf.output.flagged == plain.output.flagged
