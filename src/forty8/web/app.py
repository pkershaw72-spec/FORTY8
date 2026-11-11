"""Minimal web app: a run's progress through the 11 steps and the slip lane, the review queue,
learned mappings awaiting approval, and the audit log. A thin layer over the same services
the CLI uses; it holds no state of its own."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from forty8.config import Settings, load_settings
from forty8.models.common import MappingStatus
from forty8.orchestrator.engine import Orchestrator
from forty8.orchestrator.metrics import run_metrics
from forty8.orchestrator.report import outcome_rows, summary_across_runs

INDEX = Path(__file__).parent / "static" / "index.html"


class CorrectionIn(BaseModel):
    value: Any
    analyst: str


class ApprovalIn(BaseModel):
    analyst: str
    scope: str = "global"


class DemoIn(BaseModel):
    profile: str = "clean"


def create_app(settings: Settings | None = None, orchestrator: Orchestrator | None = None) -> FastAPI:
    settings = settings or load_settings()
    orc = orchestrator or Orchestrator(settings)
    run_lock = threading.Lock()  # one pipeline advance at a time
    app = FastAPI(title="Forty8", docs_url="/api/docs", redoc_url=None)

    def guard(fn, *args, **kwargs):
        """Translate service errors into HTTP errors with the same clear message."""
        try:
            return fn(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX.read_text(encoding="utf-8")

    @app.get("/api/runs")
    def runs() -> dict:
        rows = []
        for run in orc.store.list_runs():
            m = run_metrics(orc.store, run["run_id"])
            rows.append({**run, "straight_through": m.straight_through, "analyst_touches": m.analyst_touches,
                         "open_items": len(orc.review.list(run["run_id"]))})
        return {"runs": rows, "summary": summary_across_runs(orc),
                "thresholds": settings.thresholds.model_dump()}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict:
        run = guard(orc.store.get_run, run_id)
        return {
            "run": run,
            "metrics": run_metrics(orc.store, run_id).model_dump(mode="json"),
            "outcome": outcome_rows(orc, run_id),
            "review": [i.model_dump(mode="json") for i in orc.review.list(run_id, open_only=False)],
        }

    @app.get("/api/runs/{run_id}/audit")
    def audit(run_id: str) -> dict:
        guard(orc.store.get_run, run_id)
        return {"events": [e.model_dump(mode="json") for e in orc.audit.for_run(run_id)]}

    @app.post("/api/runs/{run_id}/resume")
    def resume(run_id: str) -> dict:
        guard(orc.store.get_run, run_id)
        with run_lock:
            status = orc.resume(run_id)
        return {"run_id": run_id, "status": status.value}

    @app.post("/api/demo")
    def demo(body: DemoIn) -> dict:
        """Generate a synthetic submission and run it (proof-of-concept convenience)."""
        from forty8.synth.generator import generate

        out_dir = Path(settings.storage.db_path).parent / "synthetic"
        sub = guard(generate, out_dir, profile=body.profile, mock_data_dir=settings.storage.mock_data_dir)
        with run_lock:
            run_id = orc.start(sub.sov_path, sub.slip_path, broker=sub.broker)
        return {"run_id": run_id, "status": orc.store.get_run(run_id)["status"]}

    @app.get("/api/review")
    def review(run_id: str | None = None, all: bool = False) -> dict:
        return {"items": [i.model_dump(mode="json") for i in orc.review.list(run_id, open_only=not all)]}

    @app.post("/api/review/{item_id}/correct")
    def correct(item_id: int, body: CorrectionIn) -> dict:
        item, mapping = guard(orc.review.correct, item_id, body.value, body.analyst)
        return {"item": item.model_dump(mode="json"),
                "proposed_mapping": mapping.model_dump(mode="json") if mapping else None}

    @app.get("/api/learn")
    def learn(status: str | None = None) -> dict:
        wanted = guard(MappingStatus, status) if status else None
        return {"mappings": [m.model_dump(mode="json") for m in orc.learning.list(wanted)]}

    @app.post("/api/learn/{mapping_id}/approve")
    def approve(mapping_id: int, body: ApprovalIn) -> dict:
        return guard(orc.learning.approve, mapping_id, body.analyst, body.scope).model_dump(mode="json")

    @app.post("/api/learn/{mapping_id}/revoke")
    def revoke(mapping_id: int, body: ApprovalIn) -> dict:
        return guard(orc.learning.revoke, mapping_id, body.analyst).model_dump(mode="json")

    return app
