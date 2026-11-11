"""SQLite store for runs, step state, artefacts, audit log, review queue and learned mappings.

One connection guarded by a lock so the slip lane's worker thread can share it.
The audit log is append-only: triggers reject UPDATE and DELETE.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from forty8.models.common import (
    AuditEvent,
    ExceptionItem,
    LearnedMapping,
    LearnSpec,
    MappingStatus,
    ReviewItem,
    ReviewStatus,
    RunStatus,
    StepStatus,
    utcnow,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    broker TEXT NOT NULL,
    sov_path TEXT NOT NULL,
    slip_path TEXT NOT NULL,
    insured TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS step_runs (
    run_id TEXT NOT NULL,
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, step)
);
CREATE TABLE IF NOT EXISTS artefacts (
    run_id TEXT NOT NULL,
    step TEXT NOT NULL,
    payload TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (run_id, step)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    input_ref TEXT NOT NULL,
    decision TEXT NOT NULL,
    confidence REAL NOT NULL,
    rule_or_model TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TABLE IF NOT EXISTS review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step TEXT NOT NULL,
    code TEXT NOT NULL,
    reason TEXT NOT NULL,
    subject TEXT NOT NULL,
    payload TEXT NOT NULL,
    priority REAL NOT NULL,
    learn TEXT,
    status TEXT NOT NULL,
    correction TEXT,
    analyst TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS learned_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    status TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    approved_by TEXT,
    source_run_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # ---- runs ---------------------------------------------------------------
    def create_run(
        self, run_id: str, broker: str, sov_path: str, slip_path: str, steps: list[str], insured: str | None = None
    ) -> None:
        now = utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,NULL)",
                (run_id, broker, sov_path, slip_path, insured, RunStatus.RUNNING, now),
            )
            self._conn.executemany(
                "INSERT INTO step_runs (run_id, step, status) VALUES (?,?,?)",
                [(run_id, s, StepStatus.PENDING) for s in steps],
            )
            self._conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if row is None:
            raise KeyError(f"unknown run {run_id}")
        return dict(row)

    def list_runs(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._all("SELECT * FROM runs ORDER BY created_at DESC")]

    def set_run_status(self, run_id: str, status: RunStatus) -> None:
        done = utcnow().isoformat() if status == RunStatus.COMPLETED else None
        self._exec("UPDATE runs SET status=?, completed_at=? WHERE run_id=?", (status, done, run_id))

    # ---- steps --------------------------------------------------------------
    def get_steps(self, run_id: str) -> dict[str, dict[str, Any]]:
        return {r["step"]: dict(r) for r in self._all("SELECT * FROM step_runs WHERE run_id=?", (run_id,))}

    def step_status(self, run_id: str, step: str) -> StepStatus:
        row = self._one("SELECT status FROM step_runs WHERE run_id=? AND step=?", (run_id, step))
        if row is None:
            raise KeyError(f"unknown step {step} for run {run_id}")
        return StepStatus(row["status"])

    def start_step(self, run_id: str, step: str) -> None:
        self._exec(
            "UPDATE step_runs SET status=?, attempts=attempts+1, started_at=COALESCE(started_at, ?) "
            "WHERE run_id=? AND step=?",
            (StepStatus.RUNNING, utcnow().isoformat(), run_id, step),
        )

    def finish_step(self, run_id: str, step: str, status: StepStatus, elapsed: float) -> None:
        self._exec(
            "UPDATE step_runs SET status=?, elapsed_seconds=elapsed_seconds+?, finished_at=? "
            "WHERE run_id=? AND step=?",
            (status, elapsed, utcnow().isoformat(), run_id, step),
        )

    # ---- artefacts ----------------------------------------------------------
    def save_artefact(self, run_id: str, step: str, payload_json: str) -> None:
        self._exec(
            "INSERT OR REPLACE INTO artefacts VALUES (?,?,?,?)",
            (run_id, step, payload_json, utcnow().isoformat()),
        )

    def load_artefact(self, run_id: str, step: str) -> str | None:
        row = self._one("SELECT payload FROM artefacts WHERE run_id=? AND step=?", (run_id, step))
        return row["payload"] if row else None

    # ---- audit log (append-only) -------------------------------------------
    def append_audit(self, event: AuditEvent) -> int:
        cur = self._exec(
            "INSERT INTO audit_log (ts, run_id, agent, input_ref, decision, confidence, rule_or_model, detail) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                event.ts.isoformat(), event.run_id, event.agent, event.input_ref, event.decision,
                event.confidence, event.rule_or_model, json.dumps(event.detail, default=str),
            ),
        )
        return int(cur.lastrowid)

    def audit_events(self, run_id: str) -> list[AuditEvent]:
        rows = self._all("SELECT * FROM audit_log WHERE run_id=? ORDER BY id", (run_id,))
        return [
            AuditEvent(
                id=r["id"], ts=datetime.fromisoformat(r["ts"]), run_id=r["run_id"], agent=r["agent"],
                input_ref=r["input_ref"], decision=r["decision"], confidence=r["confidence"],
                rule_or_model=r["rule_or_model"], detail=json.loads(r["detail"]),
            )
            for r in rows
        ]

    # ---- review queue -------------------------------------------------------
    def add_review_item(self, run_id: str, step: str, item: ExceptionItem) -> int:
        cur = self._exec(
            "INSERT INTO review_items (run_id, step, code, reason, subject, payload, priority, learn, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, step, item.code, item.reason, item.subject, json.dumps(item.payload, default=str),
                item.priority, item.learn.model_dump_json() if item.learn else None,
                ReviewStatus.OPEN, utcnow().isoformat(),
            ),
        )
        return int(cur.lastrowid)

    def get_review_item(self, item_id: int) -> ReviewItem:
        row = self._one("SELECT * FROM review_items WHERE id=?", (item_id,))
        if row is None:
            raise KeyError(f"unknown review item {item_id}")
        return _review_item(row)

    def review_items(
        self, run_id: str | None = None, step: str | None = None, status: ReviewStatus | None = None
    ) -> list[ReviewItem]:
        sql, params = "SELECT * FROM review_items WHERE 1=1", []
        for col, val in (("run_id", run_id), ("step", step), ("status", status)):
            if val is not None:
                sql += f" AND {col}=?"
                params.append(val)
        sql += " ORDER BY priority DESC, id"
        return [_review_item(r) for r in self._all(sql, tuple(params))]

    def resolve_review_item(self, item_id: int, correction: Any, analyst: str) -> None:
        self._exec(
            "UPDATE review_items SET status=?, correction=?, analyst=?, resolved_at=? WHERE id=?",
            (ReviewStatus.RESOLVED, json.dumps(correction, default=str), analyst, utcnow().isoformat(), item_id),
        )

    # ---- learned mappings ---------------------------------------------------
    def add_mapping(self, kind: str, scope: str, key: str, value: Any, proposed_by: str, run_id: str | None) -> int:
        cur = self._exec(
            "INSERT INTO learned_mappings (kind, scope, key, value, status, proposed_by, source_run_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (kind, scope, key, json.dumps(value, default=str), MappingStatus.PROPOSED, proposed_by, run_id,
             utcnow().isoformat()),
        )
        return int(cur.lastrowid)

    def get_mapping(self, mapping_id: int) -> LearnedMapping:
        row = self._one("SELECT * FROM learned_mappings WHERE id=?", (mapping_id,))
        if row is None:
            raise KeyError(f"unknown learned mapping {mapping_id}")
        return _mapping(row)

    def mappings(self, status: MappingStatus | None = None) -> list[LearnedMapping]:
        if status is None:
            rows = self._all("SELECT * FROM learned_mappings ORDER BY id")
        else:
            rows = self._all("SELECT * FROM learned_mappings WHERE status=? ORDER BY id", (status,))
        return [_mapping(r) for r in rows]

    def decide_mapping(self, mapping_id: int, status: MappingStatus, by: str, scope: str | None = None) -> None:
        self._exec(
            "UPDATE learned_mappings SET status=?, approved_by=?, decided_at=?, scope=COALESCE(?, scope) WHERE id=?",
            (status, by, utcnow().isoformat(), scope, mapping_id),
        )

    def find_approved_mapping(self, kind: str, key: str, scopes: list[str]) -> LearnedMapping | None:
        """Most recent approved mapping, trying scopes in the order given."""
        for scope in scopes:
            row = self._one(
                "SELECT * FROM learned_mappings WHERE kind=? AND key=? AND scope=? AND status=? "
                "ORDER BY id DESC LIMIT 1",
                (kind, key, scope, MappingStatus.APPROVED),
            )
            if row is not None:
                return _mapping(row)
        return None


def _review_item(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        id=row["id"], run_id=row["run_id"], step=row["step"], code=row["code"], reason=row["reason"],
        subject=row["subject"], payload=json.loads(row["payload"]), priority=row["priority"],
        learn=LearnSpec.model_validate_json(row["learn"]) if row["learn"] else None,
        status=ReviewStatus(row["status"]),
        correction=json.loads(row["correction"]) if row["correction"] is not None else None,
        analyst=row["analyst"], created_at=datetime.fromisoformat(row["created_at"]),
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
    )


def _mapping(row: sqlite3.Row) -> LearnedMapping:
    return LearnedMapping(
        id=row["id"], kind=row["kind"], scope=row["scope"], key=row["key"], value=json.loads(row["value"]),
        status=MappingStatus(row["status"]), proposed_by=row["proposed_by"], approved_by=row["approved_by"],
        source_run_id=row["source_run_id"], created_at=datetime.fromisoformat(row["created_at"]),
        decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
    )
