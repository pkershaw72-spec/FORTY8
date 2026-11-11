"""Append-only audit log writer. Every agent decision and every analyst action goes through here."""

from __future__ import annotations

from typing import Any

from forty8.models.common import AuditEntry, AuditEvent
from forty8.store.db import Store


class AuditLog:
    def __init__(self, store: Store):
        self._store = store

    def record(self, run_id: str, agent: str, entry: AuditEntry) -> int:
        return self._store.append_audit(AuditEvent(run_id=run_id, agent=agent, **entry.model_dump()))

    def note(
        self, run_id: str, agent: str, input_ref: str, decision: str,
        rule_or_model: str, confidence: float = 1.0, **detail: Any,
    ) -> int:
        entry = AuditEntry(
            input_ref=input_ref, decision=decision, confidence=confidence,
            rule_or_model=rule_or_model, detail=detail,
        )
        return self.record(run_id, agent, entry)

    def for_run(self, run_id: str) -> list[AuditEvent]:
        return self._store.audit_events(run_id)
