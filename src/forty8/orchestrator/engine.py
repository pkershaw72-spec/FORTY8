"""Explicit state-machine orchestrator.

- Main lane runs steps 1..11 in order. The slip lane (2b) starts in a worker thread as soon
  as intake passes; step 9 waits for it.
- A step that raises exceptions is parked as AWAITING_REVIEW and its lane pauses; the other
  lane carries on. `advance()` is idempotent, so resuming is just calling it again.
- The orchestrator is the only writer: outputs, audit events and review items.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from pathlib import Path

from pydantic import BaseModel

from forty8.agents.base import Agent, AgentContext
from forty8.audit import AuditLog
from forty8.config import Settings
from forty8.connectors.interfaces import Connectors
from forty8.connectors.registry import build_connectors
from forty8.learning.service import LearningService
from forty8.models.common import ResultStatus, ReviewStatus, RunStatus, StepStatus
from forty8.models.exposure import Submission
from forty8.orchestrator.pipeline import MAIN, STEPS, StepDef, default_agents, lane_steps
from forty8.review.service import ReviewService
from forty8.store.db import Store

ORCHESTRATOR = "orchestrator"


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        connectors: Connectors | None = None,
        agents: dict[str, Agent] | None = None,
    ):
        self.settings = settings
        self.store = store or Store(settings.storage.db_path)
        self.connectors = connectors or build_connectors(settings)
        self.agents = default_agents() | (agents or {})
        self.audit = AuditLog(self.store)
        self.learning = LearningService(self.store, self.audit)
        self.review = ReviewService(self.store, self.audit, self.learning)

    # ---- public API ---------------------------------------------------------
    def start(self, sov_path: str | Path, slip_path: str | Path, broker: str = "unknown", insured: str | None = None) -> str:
        """Create a run and advance it as far as it will go."""
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        self.store.create_run(run_id, broker, str(sov_path), str(slip_path), [s.key for s in STEPS], insured)
        self.audit.note(
            run_id, ORCHESTRATOR, f"run:{run_id}", "run created", rule_or_model="state_machine",
            sov=str(sov_path), slip=str(slip_path), broker=broker, insured=insured,
        )
        self.advance(run_id)
        return run_id

    def resume(self, run_id: str) -> RunStatus:
        self.audit.note(run_id, ORCHESTRATOR, f"run:{run_id}", "resume requested", rule_or_model="state_machine")
        return self.advance(run_id)

    def advance(self, run_id: str) -> RunStatus:
        """Run every lane forward until it completes or pauses. Safe to call repeatedly."""
        if self.store.get_run(run_id)["status"] in (RunStatus.COMPLETED, RunStatus.FAILED):
            return RunStatus(self.store.get_run(run_id)["status"])
        self.store.set_run_status(run_id, RunStatus.RUNNING)
        side_lanes: dict[str, threading.Thread] = {}

        def start_side_lane(lane: str) -> None:
            if lane not in side_lanes:
                thread = threading.Thread(target=self._run_lane, args=(run_id, lane), name=f"{run_id}-{lane}")
                side_lanes[lane] = thread
                thread.start()

        try:
            for step in lane_steps(MAIN):
                if step.waits_for_lane:
                    if thread := side_lanes.get(step.waits_for_lane):
                        thread.join()
                    if not self._lane_passed(run_id, step.waits_for_lane):
                        break
                if not self._ensure_passed(run_id, step):
                    break
                if step.starts_lane:
                    start_side_lane(step.starts_lane)
        finally:
            for thread in side_lanes.values():
                thread.join()

        status = self._derive_run_status(run_id)
        self.store.set_run_status(run_id, status)
        self.audit.note(run_id, ORCHESTRATOR, f"run:{run_id}", f"run {status.value}", rule_or_model="state_machine")
        return status

    def submission(self, run_id: str) -> Submission:
        run = self.store.get_run(run_id)
        return Submission(
            run_id=run_id, broker=run["broker"], sov_path=run["sov_path"], slip_path=run["slip_path"], insured=run["insured"],
        )

    def output(self, run_id: str, step: str) -> BaseModel | None:
        payload = self.store.load_artefact(run_id, step)
        return self.agents[step].output_model.model_validate_json(payload) if payload is not None else None

    # ---- internals ----------------------------------------------------------
    def _run_lane(self, run_id: str, lane: str) -> None:
        for step in lane_steps(lane):
            if not self._ensure_passed(run_id, step):
                break

    def _lane_passed(self, run_id: str, lane: str) -> bool:
        return all(self.store.step_status(run_id, s.key) == StepStatus.PASSED for s in lane_steps(lane))

    def _ensure_passed(self, run_id: str, step: StepDef) -> bool:
        status = self.store.step_status(run_id, step.key)
        if status == StepStatus.PASSED:
            return True
        if status == StepStatus.FAILED:
            return False
        if self.store.review_items(run_id=run_id, step=step.key, status=ReviewStatus.OPEN):
            return False  # still waiting for the analyst
        return self._execute(run_id, step)

    def _execute(self, run_id: str, step: StepDef) -> bool:
        agent = self.agents[step.key]
        ctx = AgentContext(
            submission=self.submission(run_id),
            settings=self.settings,
            connectors=self.connectors,
            learned=self.learning.lookup_for(self.store.get_run(run_id)["broker"]),
            corrections=self.review.corrections_for(run_id, step.key),
            load_input=lambda key: self.output(run_id, key),
        )
        self.store.start_step(run_id, step.key)
        started = time.perf_counter()
        try:
            result = agent.run(ctx)
        except Exception as exc:  # an agent crash fails the run; it is never silently swallowed
            self.store.finish_step(run_id, step.key, StepStatus.FAILED, time.perf_counter() - started)
            self.audit.note(
                run_id, step.key, f"step:{step.key}", f"step failed: {type(exc).__name__}: {exc}",
                rule_or_model="orchestrator", traceback=traceback.format_exc(limit=5),
            )
            return False
        elapsed = time.perf_counter() - started

        for entry in result.audit:
            self.audit.record(run_id, step.key, entry)
        if result.output is not None:
            self.store.save_artefact(run_id, step.key, result.output.model_dump_json(by_alias=True))

        if result.status == ResultStatus.PASS:
            self.store.finish_step(run_id, step.key, StepStatus.PASSED, elapsed)
            self.audit.note(
                run_id, ORCHESTRATOR, f"step:{step.key}", f"step {step.number} passed",
                rule_or_model="state_machine", elapsed_seconds=round(elapsed, 4),
            )
            return True

        for item in result.exceptions:
            item_id = self.store.add_review_item(run_id, step.key, item)
            self.audit.note(
                run_id, step.key, f"review_item:{item_id}", f"exception raised: {item.code}",
                rule_or_model="exception_path", reason=item.reason, subject=item.subject,
            )
        self.store.finish_step(run_id, step.key, StepStatus.AWAITING_REVIEW, elapsed)
        self.audit.note(
            run_id, ORCHESTRATOR, f"step:{step.key}",
            f"step {step.number} paused for review ({len(result.exceptions)} item(s))",
            rule_or_model="state_machine", elapsed_seconds=round(elapsed, 4),
        )
        return False

    def _derive_run_status(self, run_id: str) -> RunStatus:
        statuses = {row["status"] for row in self.store.get_steps(run_id).values()}
        if StepStatus.FAILED in statuses:
            return RunStatus.FAILED
        if statuses == {StepStatus.PASSED}:
            return RunStatus.COMPLETED
        return RunStatus.AWAITING_REVIEW
