"""Cross-cutting models: agent results, audit events, review items, learned mappings, metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    FAILED = "FAILED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ResultStatus(StrEnum):
    PASS = "PASS"
    EXCEPTION = "EXCEPTION"


class AuditEntry(BaseModel):
    """One agent decision. The orchestrator adds timestamp, run and agent."""

    input_ref: str
    decision: str
    confidence: float = Field(ge=0, le=1)
    rule_or_model: str
    detail: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(AuditEntry):
    id: int | None = None
    ts: datetime = Field(default_factory=utcnow)
    run_id: str
    agent: str


class LearnSpec(BaseModel):
    """How a correction to an exception becomes a reusable learned mapping."""

    kind: str  # e.g. "header", "occupancy", "construction", "slip_term", "address"
    key: str   # normalised lookup key, e.g. the source header text
    # Optional: what to store for each possible correction when the analyst's answer (e.g. a row
    # number) is not itself reusable. Keys are str(correction).
    values: dict[str, Any] | None = None


class ExceptionItem(BaseModel):
    """Something an agent could not decide; becomes a review-queue item."""

    code: str            # machine-readable reason, e.g. "UNMAPPED_REQUIRED_COLUMN"
    reason: str          # clear human-readable reason
    subject: str         # what the correction applies to, e.g. "header:Amt (Note 3)"
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: float = 0.0  # higher first (geocoding uses TIV)
    learn: LearnSpec | None = None


class Correction(BaseModel):
    """An analyst's answer to a review item, handed back to the agent on resume."""

    item_id: int
    code: str
    subject: str
    value: Any
    analyst: str


class AgentResult(BaseModel):
    status: ResultStatus
    output: BaseModel | None = None
    exceptions: list[ExceptionItem] = Field(default_factory=list)
    audit: list[AuditEntry] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class ReviewStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ReviewItem(BaseModel):
    id: int
    run_id: str
    step: str
    code: str
    reason: str
    subject: str
    payload: dict[str, Any]
    priority: float
    learn: LearnSpec | None
    status: ReviewStatus
    correction: Any = None
    analyst: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class MappingStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REVOKED = "revoked"


GLOBAL_SCOPE = "global"


class LearnedMapping(BaseModel):
    id: int
    kind: str
    scope: str  # "global" or "broker:<name>"
    key: str
    value: Any
    status: MappingStatus
    proposed_by: str
    approved_by: str | None = None
    source_run_id: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class StepMetrics(BaseModel):
    step: str
    number: str
    name: str
    lane: str
    status: StepStatus
    attempts: int
    elapsed_seconds: float
    exceptions_raised: int
    analyst_touches: int


class RunMetrics(BaseModel):
    run_id: str
    status: RunStatus
    straight_through: bool
    elapsed_seconds: float       # sum of agent processing time
    wall_clock_seconds: float | None  # created -> completed, includes analyst waiting time
    exceptions_raised: int
    analyst_touches: int
    steps: list[StepMetrics]
