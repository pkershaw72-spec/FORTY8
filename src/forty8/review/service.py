"""Review queue: analysts see exceptions, correct them, and the run resumes."""

from __future__ import annotations

from typing import Any

from forty8.audit import AuditLog
from forty8.learning.service import LearningService
from forty8.models.common import Correction, LearnedMapping, ReviewItem, ReviewStatus
from forty8.store.db import Store


class ReviewService:
    def __init__(self, store: Store, audit: AuditLog, learning: LearningService):
        self._store = store
        self._audit = audit
        self._learning = learning

    def list(self, run_id: str | None = None, open_only: bool = True) -> list[ReviewItem]:
        return self._store.review_items(run_id=run_id, status=ReviewStatus.OPEN if open_only else None)

    def get(self, item_id: int) -> ReviewItem:
        return self._store.get_review_item(item_id)

    def correct(self, item_id: int, value: Any, analyst: str) -> tuple[ReviewItem, LearnedMapping | None]:
        """Record the analyst's correction. If the item is learnable, propose a learned mapping
        (reused on later submissions only after a second analyst approves it)."""
        item = self._store.get_review_item(item_id)
        if item.status != ReviewStatus.OPEN:
            raise ValueError(f"review item {item_id} is already resolved")
        if not analyst.strip():
            raise ValueError("analyst name is required")
        self._store.resolve_review_item(item_id, value, analyst)
        self._audit.note(
            item.run_id, "review", f"review_item:{item_id}", f"analyst corrected {item.code}",
            rule_or_model=f"analyst:{analyst}", step=item.step, subject=item.subject, value=value,
        )
        mapping = None
        if item.learn is not None:
            broker = self._store.get_run(item.run_id)["broker"]
            mapping = self._learning.propose(item.learn.kind, item.learn.key, value, analyst, broker, item.run_id)
        return self._store.get_review_item(item_id), mapping

    def corrections_for(self, run_id: str, step: str) -> list[Correction]:
        items = self._store.review_items(run_id=run_id, step=step, status=ReviewStatus.RESOLVED)
        return [
            Correction(item_id=i.id, code=i.code, subject=i.subject, value=i.correction, analyst=i.analyst or "")
            for i in items
        ]
