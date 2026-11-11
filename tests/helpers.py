"""Test helpers: build an agent context without a database, and answer review items from truth."""

from __future__ import annotations

from typing import Any

from forty8.agents.base import AgentContext
from forty8.connectors.registry import build_connectors
from forty8.models.common import Correction, LearnedMapping, MappingStatus, ReviewItem, utcnow
from forty8.models.exposure import Submission
from forty8.reference.gazetteer import find_city


class DictLearning:
    """In-memory stand-in for approved learned mappings: {(kind, key): value}."""

    def __init__(self, mappings: dict[tuple[str, str], Any] | None = None):
        self._mappings = {(k, key.strip().lower()): v for (k, key), v in (mappings or {}).items()}

    def lookup(self, kind: str, key: str) -> LearnedMapping | None:
        norm = " ".join(key.strip().lower().split())
        if (kind, norm) not in self._mappings:
            return None
        return LearnedMapping(id=99, kind=kind, scope="global", key=norm, value=self._mappings[(kind, norm)],
                              status=MappingStatus.APPROVED, proposed_by="alice", approved_by="bob",
                              created_at=utcnow())


def make_ctx(settings, inputs=None, corrections=None, learned=None, connectors=None,
             sov="sov.xlsx", slip="slip.docx", insured=None) -> AgentContext:
    fixes = [Correction(item_id=i, code="TEST", subject=subject, value=value, analyst="alice")
             for i, (subject, value) in enumerate((corrections or {}).items())]
    return AgentContext(
        submission=Submission(run_id="run_test", broker="Test Broker", sov_path=str(sov), slip_path=str(slip),
                              insured=insured),
        settings=settings, connectors=connectors or build_connectors(settings),
        learned=DictLearning(learned), corrections=fixes, load_input=lambda step: (inputs or {}).get(step),
    )


def answer(item: ReviewItem, submission) -> Any:
    """What a well-informed analyst would answer, taken from the synthetic truth."""
    slip, payload = submission.slip, item.payload
    if item.code == "AMBIGUOUS_HEADER_ROW":
        return next(int(row) for row, cells in payload["candidates"].items() if "Loc #" in cells)
    if item.code == "UNMAPPED_REQUIRED_COLUMN":
        return payload["missing_required"][0]
    if item.code == "LOW_CONFIDENCE_CLASSIFICATION":
        loc = next(l for l in submission.locations if l.loc_number == payload["locations"][0])
        return getattr(loc, payload["field"])
    if item.code == "LOW_CONFIDENCE_SLIP_TERM":
        name = payload["term"]
        if name.startswith("sublimit:"):
            return payload["extracted_value"]["limit"]
        return getattr(slip, name)
    if item.code == "LOW_TIV_GEOCODE":
        loc = next(l for l in submission.locations if l.loc_number == payload["locations"][0])
        city = find_city(loc.city)
        return f"{city.lat}, {city.lon}"
    if item.code == "LARGE_YOY_CHANGE":
        return "confirm"
    fixed = {"AUDIT_TIV_RECONCILE": "accept", "AUDIT_YEAR_BUILT": 1998, "AUDIT_DUPLICATE": "remove",
             "AUDIT_CONSTRUCTION_STOREYS": {"NumberOfStoreys": 2}, "UNDEFINED_SUBLIMIT_ZONE": "all",
             "MODEL_JOB_FAILED": "retry", "PRICING_REJECTED": "retry", "UNREADABLE_FILE": submission.sov_path}
    if item.code in fixed:
        return fixed[item.code]
    raise AssertionError(f"no scripted answer for {item.code}")
