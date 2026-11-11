"""Agent contract. An agent is a small pure unit: it reads its context and returns an
AgentResult. It never writes to the database; the orchestrator persists everything."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from pydantic import BaseModel

from forty8.config import Settings
from forty8.connectors.interfaces import Connectors
from forty8.learning.service import LearnedLookup, NoLearning
from forty8.models.common import AgentResult, AuditEntry, Correction, ResultStatus
from forty8.models.exposure import Submission


@dataclass
class AgentContext:
    submission: Submission
    settings: Settings
    connectors: Connectors
    learned: LearnedLookup = field(default_factory=NoLearning)
    corrections: list[Correction] = field(default_factory=list)
    # Returns the typed output of an earlier step (None if that step has no output yet).
    load_input: Callable[[str], BaseModel | None] = lambda step: None

    @property
    def run_id(self) -> str:
        return self.submission.run_id

    def input(self, step: str) -> Any:
        output = self.load_input(step)
        if output is None:
            raise LookupError(f"no output available from step '{step}'")
        return output

    def correction_for(self, subject: str, code: str | None = None) -> Correction | None:
        """Latest analyst correction for a subject (optionally a specific exception code)."""
        matches = [c for c in self.corrections if c.subject == subject and (code is None or c.code == code)]
        return matches[-1] if matches else None


class Agent(Protocol):
    key: str
    output_model: type[BaseModel]

    def run(self, ctx: AgentContext) -> AgentResult: ...


class Passthrough(BaseModel):
    """Output of a placeholder step."""

    step: str
    note: str = "placeholder"


class NoOpAgent:
    """Placeholder used until a step's real agent is implemented."""

    output_model = Passthrough

    def __init__(self, key: str):
        self.key = key

    def run(self, ctx: AgentContext) -> AgentResult:
        return AgentResult(
            status=ResultStatus.PASS,
            output=Passthrough(step=self.key),
            audit=[AuditEntry(
                input_ref=f"run:{ctx.run_id}", decision="passed through (placeholder step)",
                confidence=1.0, rule_or_model="noop",
            )],
        )
