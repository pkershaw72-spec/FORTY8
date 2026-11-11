"""Phase 1: the empty pipeline (placeholder agents) runs end to end."""

from forty8.models.common import RunStatus, StepStatus
from forty8.orchestrator.metrics import run_metrics
from forty8.orchestrator.pipeline import STEPS


def test_empty_pipeline_runs_end_to_end(make_orchestrator, clean_submission):
    orc = make_orchestrator()
    run_id = orc.start(clean_submission.sov_path, clean_submission.slip_path, broker=clean_submission.broker)

    assert orc.store.get_run(run_id)["status"] == RunStatus.COMPLETED
    steps = orc.store.get_steps(run_id)
    assert len(steps) == 12  # 11 steps plus the slip lane
    assert all(row["status"] == StepStatus.PASSED and row["attempts"] == 1 for row in steps.values())

    # every step wrote an output and at least one audit event with confidence and rule/model
    events = orc.audit.for_run(run_id)
    for step in STEPS:
        assert orc.output(run_id, step.key) is not None
        assert any(e.agent == step.key and e.rule_or_model and 0 <= e.confidence <= 1 for e in events)

    metrics = run_metrics(orc.store, run_id)
    assert metrics.straight_through is True
    assert metrics.analyst_touches == 0
    assert [s.number for s in metrics.steps] == ["1", "2", "2b", "3", "4", "5", "6", "7", "8", "9", "10", "11"]
