"""Orchestrator behaviour: pause on exception, correct, resume; slip lane join; crash handling."""

from __future__ import annotations

import sqlite3

import pytest

from forty8.agents.base import AgentContext, Passthrough
from forty8.models.common import (
    AgentResult, AuditEntry, ExceptionItem, LearnSpec, MappingStatus, ResultStatus, RunStatus, StepStatus,
)
from forty8.orchestrator.metrics import run_metrics


class NeedsAnalyst:
    """Raises one exception until an analyst has corrected it (or an approved learned mapping exists)."""

    output_model = Passthrough

    def __init__(self, key: str):
        self.key = key

    def run(self, ctx: AgentContext) -> AgentResult:
        subject = "header:Amt (Note 3)"
        learned = ctx.learned.lookup("header", "Amt (Note 3)")
        fix = ctx.correction_for(subject)
        if learned or fix:
            how = f"learned_mapping:{learned.id}" if learned else f"analyst:{fix.analyst}"
            value = learned.value if learned else fix.value
            return AgentResult(
                status=ResultStatus.PASS, output=Passthrough(step=self.key, note=str(value)),
                audit=[AuditEntry(input_ref=subject, decision=f"mapped to {value}", confidence=1.0, rule_or_model=how)],
            )
        return AgentResult(
            status=ResultStatus.EXCEPTION,
            exceptions=[ExceptionItem(
                code="UNMAPPED_REQUIRED_COLUMN", reason="No target field for header 'Amt (Note 3)'",
                subject=subject, learn=LearnSpec(kind="header", key="Amt (Note 3)"),
            )],
        )


class Crashes:
    output_model = Passthrough
    key = "geocoding"

    def run(self, ctx: AgentContext) -> AgentResult:
        raise RuntimeError("boom")


def _start(orc, sub):
    return orc.start(sub.sov_path, sub.slip_path, broker=sub.broker)


def test_exception_pauses_then_correction_resumes(make_orchestrator, clean_submission):
    orc = make_orchestrator({"schema_mapping": NeedsAnalyst("schema_mapping")})
    run_id = _start(orc, clean_submission)

    assert orc.store.get_run(run_id)["status"] == RunStatus.AWAITING_REVIEW
    steps = orc.store.get_steps(run_id)
    assert steps["schema_mapping"]["status"] == StepStatus.AWAITING_REVIEW
    assert steps["coding"]["status"] == StepStatus.PENDING
    assert steps["contract"]["status"] == StepStatus.PASSED  # slip lane carried on in parallel

    [item] = orc.review.list(run_id)
    assert orc.resume(run_id) == RunStatus.AWAITING_REVIEW  # nothing corrected yet: still paused

    _, mapping = orc.review.correct(item.id, "BuildingTIV", analyst="alice")
    assert mapping.status == MappingStatus.PROPOSED
    assert orc.resume(run_id) == RunStatus.COMPLETED
    assert orc.output(run_id, "schema_mapping").note == "BuildingTIV"

    metrics = run_metrics(orc.store, run_id)
    assert metrics.straight_through is False
    assert metrics.analyst_touches == 1
    assert next(s for s in metrics.steps if s.step == "schema_mapping").attempts == 2


def test_slip_lane_exception_blocks_policy_mapping(make_orchestrator, clean_submission):
    orc = make_orchestrator({"contract": NeedsAnalyst("contract")})
    run_id = _start(orc, clean_submission)

    steps = orc.store.get_steps(run_id)
    assert steps["contract"]["status"] == StepStatus.AWAITING_REVIEW
    assert steps["audit_gate"]["status"] == StepStatus.PASSED      # main lane ran up to the join
    assert steps["policy_mapping"]["status"] == StepStatus.PENDING

    [item] = orc.review.list(run_id)
    orc.review.correct(item.id, "PolLimit", analyst="alice")
    assert orc.resume(run_id) == RunStatus.COMPLETED


def test_learned_mapping_needs_second_analyst_before_reuse(make_orchestrator, clean_submission):
    orc = make_orchestrator({"schema_mapping": NeedsAnalyst("schema_mapping")})
    first = _start(orc, clean_submission)
    [item] = orc.review.list(first)
    _, mapping = orc.review.correct(item.id, "BuildingTIV", analyst="alice")
    orc.resume(first)

    # proposed only: the next submission still raises the exception
    second = _start(orc, clean_submission)
    assert orc.store.get_run(second)["status"] == RunStatus.AWAITING_REVIEW

    with pytest.raises(PermissionError):
        orc.learning.approve(mapping.id, analyst="Alice")  # same analyst cannot approve their own correction
    orc.learning.approve(mapping.id, analyst="bob")

    third = _start(orc, clean_submission)
    assert orc.store.get_run(third)["status"] == RunStatus.COMPLETED
    assert run_metrics(orc.store, third).straight_through is True
    assert any(e.rule_or_model == f"learned_mapping:{mapping.id}" for e in orc.audit.for_run(third))

    orc.learning.revoke(mapping.id, analyst="carol")
    fourth = _start(orc, clean_submission)
    assert orc.store.get_run(fourth)["status"] == RunStatus.AWAITING_REVIEW


def test_agent_crash_fails_the_run_and_is_audited(make_orchestrator, clean_submission):
    orc = make_orchestrator({"geocoding": Crashes()})
    run_id = _start(orc, clean_submission)
    assert orc.store.get_run(run_id)["status"] == RunStatus.FAILED
    assert orc.store.get_steps(run_id)["enrichment"]["status"] == StepStatus.PENDING
    assert any("RuntimeError: boom" in e.decision for e in orc.audit.for_run(run_id))


def test_audit_log_is_append_only(make_orchestrator, clean_submission):
    orc = make_orchestrator()
    run_id = _start(orc, clean_submission)
    for statement in ("UPDATE audit_log SET decision='tampered'", "DELETE FROM audit_log"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            orc.store._exec(statement)
    assert len(orc.audit.for_run(run_id)) > 12
