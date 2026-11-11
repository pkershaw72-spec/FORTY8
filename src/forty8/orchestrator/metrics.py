"""Run metrics: elapsed time per step, straight-through yes/no, analyst touches."""

from __future__ import annotations

from datetime import datetime

from forty8.models.common import ReviewStatus, RunMetrics, RunStatus, StepMetrics, StepStatus
from forty8.orchestrator.pipeline import STEPS
from forty8.store.db import Store


def run_metrics(store: Store, run_id: str) -> RunMetrics:
    run = store.get_run(run_id)
    step_rows = store.get_steps(run_id)
    items = store.review_items(run_id=run_id)
    steps = []
    for step in STEPS:
        row = step_rows[step.key]
        mine = [i for i in items if i.step == step.key]
        steps.append(StepMetrics(
            step=step.key, number=step.number, name=step.name, lane=step.lane,
            status=StepStatus(row["status"]), attempts=row["attempts"],
            elapsed_seconds=round(row["elapsed_seconds"], 4),
            exceptions_raised=len(mine),
            analyst_touches=sum(1 for i in mine if i.status == ReviewStatus.RESOLVED),
        ))
    status = RunStatus(run["status"])
    wall = None
    if run["completed_at"]:
        wall = (datetime.fromisoformat(run["completed_at"]) - datetime.fromisoformat(run["created_at"])).total_seconds()
    return RunMetrics(
        run_id=run_id, status=status,
        straight_through=status == RunStatus.COMPLETED and not items,
        elapsed_seconds=round(sum(s.elapsed_seconds for s in steps), 4),
        wall_clock_seconds=wall,
        exceptions_raised=len(items),
        analyst_touches=sum(s.analyst_touches for s in steps),
        steps=steps,
    )
