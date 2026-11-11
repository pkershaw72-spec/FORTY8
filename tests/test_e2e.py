"""End to end against mocks: a clean case straight through, and a messy case that triggers
every exception path, is corrected through the review queue, and completes."""

import csv

import pytest

from forty8.cli import main
from forty8.export import export_oed
from forty8.models.common import MappingStatus, RunStatus, StepStatus
from forty8.orchestrator.metrics import run_metrics
from forty8.orchestrator.pipeline import STEPS
from forty8.orchestrator.report import render_report, summary_across_runs
from forty8.synth.generator import write_unreadable_file
from helpers import answer

EVERY_EXCEPTION_PATH = {
    "intake": {"UNREADABLE_FILE"},
    "cleaning": {"AMBIGUOUS_HEADER_ROW"},
    "contract": {"LOW_CONFIDENCE_SLIP_TERM"},
    "schema_mapping": {"UNMAPPED_REQUIRED_COLUMN"},
    "coding": set(),                                   # step 4 hands over to step 5 instead
    "ml_classify": {"LOW_CONFIDENCE_CLASSIFICATION"},
    "geocoding": {"LOW_TIV_GEOCODE"},
    "enrichment": {"LARGE_YOY_CHANGE"},
    "audit_gate": {"AUDIT_DUPLICATE", "AUDIT_YEAR_BUILT", "AUDIT_CONSTRUCTION_STOREYS", "AUDIT_TIV_RECONCILE"},
    "policy_mapping": {"UNDEFINED_SUBLIMIT_ZONE"},
    "model_track": {"MODEL_JOB_FAILED"},
    "pricing": {"PRICING_REJECTED"},
}


def work_queue(orc, run_id, sub, analyst="alice"):
    """Answer every open item from the synthetic truth and resume, until nothing is left."""
    seen = []
    for _ in range(20):
        items = orc.review.list(run_id)
        if not items:
            break
        for item in items:
            orc.review.correct(item.id, answer(item, sub), analyst)
            seen.append((item.step, item.code))
        orc.resume(run_id)
    return seen


def test_clean_case_runs_straight_through(make_orchestrator, clean_submission):
    orc = make_orchestrator(real=True)
    run_id = orc.start(clean_submission.sov_path, clean_submission.slip_path, broker=clean_submission.broker)

    assert orc.store.get_run(run_id)["status"] == RunStatus.COMPLETED
    assert all(row["status"] == StepStatus.PASSED and row["attempts"] == 1 for row in orc.store.get_steps(run_id).values())
    metrics = run_metrics(orc.store, run_id)
    assert metrics.straight_through and metrics.analyst_touches == 0 and metrics.exceptions_raised == 0
    assert all(s.elapsed_seconds >= 0 for s in metrics.steps) and len(metrics.steps) == 12

    truth = {loc.loc_number: loc for loc in clean_submission.locations}
    exposure = orc.output(run_id, "policy_mapping").exposure
    assert len(exposure.locations) == 24
    for loc in exposure.locations:
        expected = truth[loc.LocNumber]
        assert (loc.OccupancyCode, loc.ConstructionCode) == (expected.occupancy_code, expected.construction_code)
        assert loc.geocode.resolution == "rooftop" and loc.h3_cell and loc.terms is not None
    slip = clean_submission.slip
    assert (exposure.account.PolLimit, exposure.account.PolDed) == (slip.limit, slip.deductible)
    assert all(t.passed and not t.failures for t in orc.output(run_id, "audit_gate").tests)

    model, pricing = orc.output(run_id, "model_track"), orc.output(run_id, "pricing")
    assert model.total_tiv == pytest.approx(slip.total_tiv_usd)
    assert 0 < model.results.aal < model.results.oep_100 < model.results.oep_250 <= slip.limit
    assert pricing.response.accepted and pricing.submission.oep_250 == model.results.oep_250

    events = orc.audit.for_run(run_id)
    assert {s.key for s in STEPS} <= {e.agent for e in events}          # every agent logged its decisions
    assert all(e.rule_or_model and 0 <= e.confidence <= 1 and e.ts for e in events)
    assert not any(e.rule_or_model.startswith(("analyst:", "learned_mapping:")) for e in events)


def test_messy_case_triggers_every_exception_path_and_completes(make_orchestrator, messy_submission, settings, tmp_path):
    settings.connectors["cat_model"].options["fail_first_n_jobs"] = settings.model.max_retries + 1
    settings.connectors["pricing_tool"].options["reject_first_n"] = 1
    orc = make_orchestrator(real=True)
    sub = messy_submission
    broken = write_unreadable_file(tmp_path / "broken.xlsx")

    run_id = orc.start(broken, sub.slip_path, broker=sub.broker)
    assert orc.store.get_run(run_id)["status"] == RunStatus.AWAITING_REVIEW
    seen = work_queue(orc, run_id, sub)

    raised = {step: {code for s, code in seen if s == step} for step in EVERY_EXCEPTION_PATH}
    assert raised == EVERY_EXCEPTION_PATH
    assert orc.store.get_run(run_id)["status"] == RunStatus.COMPLETED

    metrics = run_metrics(orc.store, run_id)
    assert not metrics.straight_through and metrics.analyst_touches == len(seen) == metrics.exceptions_raised
    for step in metrics.steps:
        assert (step.exceptions_raised > 0) == (step.step != "coding"), step.step
        assert step.attempts == (1 if step.step == "coding" else 2), step.step

    # the result is right, not just finished
    truth = {loc.loc_number: loc for loc in sub.locations}
    exposure = orc.output(run_id, "policy_mapping").exposure
    assert sorted(l.LocNumber for l in exposure.locations) == sorted(truth)        # duplicate removed
    for loc in exposure.locations:
        expected = truth[loc.LocNumber]
        assert (loc.OccupancyCode, loc.ConstructionCode) == (expected.occupancy_code, expected.construction_code)
        assert loc.BuildingTIV == expected.building and loc.terms is not None
        assert {"all"} <= set(loc.zones)
    assert next(l for l in exposure.locations if "year_out_of_range" in truth[l.LocNumber].defects).YearBuilt == 1998
    assert (exposure.account.PolLimit, exposure.account.PolDed) == (sub.slip.limit, sub.slip.deductible)
    assert orc.output(run_id, "geocoding").tiv_street_share >= 0.80
    gate = orc.output(run_id, "audit_gate")
    assert all(t.passed for t in gate.tests) and sum(t.waived for t in gate.tests) == 4
    model = orc.output(run_id, "model_track")
    assert model.total_tiv == pytest.approx(sub.slip.total_tiv_usd, rel=1e-6)
    assert model.attempts == 1 and model.job_id == "job-0004"                       # three failed jobs came first
    assert orc.output(run_id, "pricing").response.accepted

    # every analyst action is in the audit log
    events = orc.audit.for_run(run_id)
    assert sum(e.agent == "review" for e in events) == len(seen)
    assert sum("retrying" in e.decision or "retries exhausted" in e.decision for e in events) == 3

    # learning loop: proposed corrections are not reused until a second analyst approves them
    proposed = orc.learning.list(MappingStatus.PROPOSED)
    assert {m.kind for m in proposed} == {"header_row", "slip_term", "header", "occupancy", "construction",
                                          "address", "yoy_change", "zone"}
    again = orc.start(sub.sov_path, sub.slip_path, broker=sub.broker)
    assert {i.code for i in orc.review.list(again)} == {"AMBIGUOUS_HEADER_ROW", "LOW_CONFIDENCE_SLIP_TERM"}

    for mapping in proposed:
        orc.learning.approve(mapping.id, analyst="bob")
    learned = orc.start(sub.sov_path, sub.slip_path, broker=sub.broker)
    remaining = work_queue(orc, learned, sub)
    assert {step for step, _ in remaining} == {"audit_gate"}            # only the submission-specific data fixes
    assert len(remaining) == 4 < len(seen)
    assert orc.store.get_run(learned)["status"] == RunStatus.COMPLETED
    reused = {e.agent for e in orc.audit.for_run(learned) if e.rule_or_model.startswith("learned_mapping:")}
    assert reused == {"cleaning", "contract", "schema_mapping", "coding", "geocoding", "enrichment", "policy_mapping"}
    relearned = orc.output(learned, "model_track").results                # same answer with far fewer touches
    assert (relearned.aal, relearned.oep_100, relearned.oep_250) == (model.results.aal, model.results.oep_100, model.results.oep_250)


def test_broker_scoped_learning_does_not_leak(make_orchestrator, messy_submission):
    orc, sub = make_orchestrator(real=True), messy_submission
    first = orc.start(sub.sov_path, sub.slip_path, broker=sub.broker)
    work_queue(orc, first, sub)
    for mapping in orc.learning.list(MappingStatus.PROPOSED):
        orc.learning.approve(mapping.id, analyst="bob", scope="broker")
    other = orc.start(sub.sov_path, sub.slip_path, broker="Another Broker")
    assert {i.code for i in orc.review.list(other)} == {"AMBIGUOUS_HEADER_ROW", "LOW_CONFIDENCE_SLIP_TERM"}
    same = orc.start(sub.sov_path, sub.slip_path, broker=sub.broker)
    assert {i.step for i in orc.review.list(same)} == {"audit_gate"}


def test_report_export_and_summary(make_orchestrator, clean_submission, messy_submission, tmp_path):
    orc = make_orchestrator(real=True)
    clean = orc.start(clean_submission.sov_path, clean_submission.slip_path, broker=clean_submission.broker)
    messy = orc.start(messy_submission.sov_path, messy_submission.slip_path, broker=messy_submission.broker)

    report = render_report(orc, clean)
    for expected in ("| Straight-through | yes |", "| Analyst touches | 0 |", "within target", "| 2b | Contract (slip lane) |",
                     "| TIV at street level or better | 100.0% (threshold 80%) |", "| OEP 250-year | USD", "accepted, reference PRC-"):
        assert expected in report, expected
    paused = render_report(orc, messy)
    assert "| Status | AWAITING_REVIEW |" in paused and "n/a (run not complete)" in paused and "## Exceptions" in paused

    work_queue(orc, messy, messy_submission)
    done = render_report(orc, messy)
    assert "| Straight-through | no |" in done and "| AUDIT_DUPLICATE | audit_gate | 1 | 1 |" in done
    assert "tiv_reconciles_to_sov: resolved by analyst" in done

    summary = summary_across_runs(orc)
    assert (summary["runs"], summary["completed"], summary["straight_through"], summary["straight_through_rate"]) == (2, 2, 1, 0.5)

    location_csv, account_csv = export_oed(orc.output(clean, "policy_mapping").exposure, tmp_path / "export")
    locations = list(csv.DictReader(open(location_csv, encoding="utf-8")))
    accounts = list(csv.DictReader(open(account_csv, encoding="utf-8")))
    assert len(locations) == 24 and len(accounts) == 4                       # one account row per sublimit
    first = locations[0]
    assert first["OccupancyCode"] and first["Latitude"] and first["LocPerilsCovered"] == "WW1;QQ1;OO1"
    assert first["AccNumber"] == accounts[0]["AccNumber"] == clean_submission.slip.umr
    assert float(accounts[0]["LayerLimit"]) == clean_submission.slip.limit


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORTY8_DB", str(tmp_path / "cli.db"))
    monkeypatch.delenv("FORTY8_CONFIG", raising=False)

    assert main(["synth", "--profile", "clean", "--out", "synthetic"]) == 0
    assert main(["run", "synthetic/sov_clean.xlsx", "synthetic/slip_clean.docx", "--broker", "Northlight"]) == 0
    out = capsys.readouterr().out
    run_id = next(word.rstrip(":") for word in out.split() if word.startswith("run_"))
    assert f"Run {run_id}: COMPLETED" in out

    for command in (["status"], ["status", run_id], ["audit", run_id], ["metrics", run_id], ["metrics"],
                    ["report", run_id], ["review", "list", "--all"], ["learn", "list"],
                    ["export", run_id, "--out", "export"], ["report", run_id, "--out", "report.md"]):
        assert main(command) == 0, command
    out = capsys.readouterr().out
    assert "Straight-through: yes" in out and "straight through rate: 1.0" in out and "step 11 passed" in out
    assert (tmp_path / "export" / "location.csv").exists() and (tmp_path / "report.md").exists()
    assert main(["audit", "run_does_not_exist"]) == 0      # an unknown run simply has no audit entries
    assert main(["status", "run_does_not_exist"]) == 1
