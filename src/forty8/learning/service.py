"""Learning loop: analyst corrections become learned mappings, reused once a second analyst approves."""

from __future__ import annotations

from typing import Any, Protocol

from forty8.audit import AuditLog
from forty8.models.common import GLOBAL_SCOPE, LearnedMapping, MappingStatus
from forty8.store.db import Store

LEARNING_RUN = "learning"  # audit run_id used when an action is not tied to a run


def normalise_key(key: str) -> str:
    return " ".join(str(key).strip().lower().split())


def broker_scope(broker: str) -> str:
    return f"broker:{normalise_key(broker)}"


class LearnedLookup(Protocol):
    """Read-only view handed to agents."""

    def lookup(self, kind: str, key: str) -> LearnedMapping | None: ...


class NoLearning:
    def lookup(self, kind: str, key: str) -> LearnedMapping | None:
        return None


class LearningService:
    def __init__(self, store: Store, audit: AuditLog):
        self._store = store
        self._audit = audit

    def propose(self, kind: str, key: str, value: Any, analyst: str, broker: str, run_id: str | None) -> LearnedMapping:
        mapping_id = self._store.add_mapping(kind, broker_scope(broker), normalise_key(key), value, analyst, run_id)
        self._audit.note(
            run_id or LEARNING_RUN, "learning", f"mapping:{mapping_id}", "proposed learned mapping",
            rule_or_model=f"analyst:{analyst}", kind=kind, key=key, value=value,
        )
        return self._store.get_mapping(mapping_id)

    def approve(self, mapping_id: int, analyst: str, scope: str = GLOBAL_SCOPE) -> LearnedMapping:
        """Second-analyst approval. scope is "global" or "broker" (keep it to the originating broker)."""
        mapping = self._store.get_mapping(mapping_id)
        if mapping.status != MappingStatus.PROPOSED:
            raise ValueError(f"mapping {mapping_id} is {mapping.status.value}, not proposed")
        if normalise_key(analyst) == normalise_key(mapping.proposed_by):
            raise PermissionError("a learned mapping must be approved by a different analyst from the one who proposed it")
        if scope not in (GLOBAL_SCOPE, "broker"):
            raise ValueError("scope must be 'global' or 'broker'")
        new_scope = GLOBAL_SCOPE if scope == GLOBAL_SCOPE else None
        self._store.decide_mapping(mapping_id, MappingStatus.APPROVED, analyst, new_scope)
        self._audit.note(
            mapping.source_run_id or LEARNING_RUN, "learning", f"mapping:{mapping_id}",
            "approved learned mapping", rule_or_model=f"analyst:{analyst}", scope=scope,
        )
        return self._store.get_mapping(mapping_id)

    def revoke(self, mapping_id: int, analyst: str) -> LearnedMapping:
        mapping = self._store.get_mapping(mapping_id)
        self._store.decide_mapping(mapping_id, MappingStatus.REVOKED, analyst)
        self._audit.note(
            mapping.source_run_id or LEARNING_RUN, "learning", f"mapping:{mapping_id}",
            "revoked learned mapping", rule_or_model=f"analyst:{analyst}",
        )
        return self._store.get_mapping(mapping_id)

    def list(self, status: MappingStatus | None = None) -> list[LearnedMapping]:
        return self._store.mappings(status)

    def lookup_for(self, broker: str) -> "BrokerLookup":
        return BrokerLookup(self._store, broker)


class BrokerLookup:
    """Approved mappings only: this broker's first, then global."""

    def __init__(self, store: Store, broker: str):
        self._store = store
        self._scopes = [broker_scope(broker), GLOBAL_SCOPE]

    def lookup(self, kind: str, key: str) -> LearnedMapping | None:
        return self._store.find_approved_mapping(kind, normalise_key(key), self._scopes)
