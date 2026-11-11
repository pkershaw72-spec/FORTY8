"""The pipeline definition: 11 main-lane steps plus the parallel slip lane (2b)."""

from __future__ import annotations

from dataclasses import dataclass

from forty8.agents.base import Agent, NoOpAgent

MAIN = "main"
SLIP = "slip"


@dataclass(frozen=True)
class StepDef:
    key: str
    number: str
    name: str
    lane: str
    starts_lane: str | None = None   # once this step passes, the named lane starts in parallel
    waits_for_lane: str | None = None  # this step cannot start until the named lane has passed


STEPS: tuple[StepDef, ...] = (
    StepDef("intake", "1", "Intake", MAIN, starts_lane=SLIP),
    StepDef("cleaning", "2", "Cleaning", MAIN),
    StepDef("contract", "2b", "Contract (slip lane)", SLIP),
    StepDef("schema_mapping", "3", "Schema mapping", MAIN),
    StepDef("coding", "4", "Coding", MAIN),
    StepDef("ml_classify", "5", "ML classification", MAIN),
    StepDef("geocoding", "6", "Geocoding", MAIN),
    StepDef("enrichment", "7", "Enrichment", MAIN),
    StepDef("audit_gate", "8", "Audit gate", MAIN),
    StepDef("policy_mapping", "9", "Policy mapping", MAIN, waits_for_lane=SLIP),
    StepDef("model_track", "10", "Model and track", MAIN),
    StepDef("pricing", "11", "Pricing", MAIN),
)

STEP_BY_KEY = {s.key: s for s in STEPS}


def lane_steps(lane: str) -> list[StepDef]:
    return [s for s in STEPS if s.lane == lane]


def placeholder_agents() -> dict[str, Agent]:
    return {s.key: NoOpAgent(s.key) for s in STEPS}


def default_agents() -> dict[str, Agent]:
    """Real agents where implemented; placeholders elsewhere. Later phases replace the placeholders."""
    from forty8.agents.cleaning import CleaningAgent
    from forty8.agents.coding import CodingAgent
    from forty8.agents.contract import ContractAgent
    from forty8.agents.intake import IntakeAgent
    from forty8.agents.ml_classify import MLClassifyAgent
    from forty8.agents.schema_mapping import SchemaMappingAgent

    real: list[Agent] = [IntakeAgent(), CleaningAgent(), ContractAgent(), SchemaMappingAgent(), CodingAgent(),
                         MLClassifyAgent()]
    return placeholder_agents() | {agent.key: agent for agent in real}
