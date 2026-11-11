"""Phase 5 - web page and its API: run progress, slip lane, review queue, learning, audit log."""

import pytest
from fastapi.testclient import TestClient

from forty8.web.app import create_app
from helpers import answer


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


def test_page_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    for marker in ("<title>Forty8</title>", "Review queue", "Audit log", "Slip lane", "Learned mappings"):
        assert marker in page.text


def test_clean_demo_run_shows_progress_outcome_and_audit(client):
    assert client.get("/api/runs").json()["runs"] == []
    started = client.post("/api/demo", json={"profile": "clean"}).json()
    assert started["status"] == "COMPLETED"

    listing = client.get("/api/runs").json()
    assert listing["runs"][0]["run_id"] == started["run_id"] and listing["runs"][0]["straight_through"] is True
    assert listing["summary"]["straight_through_rate"] == 1.0 and listing["thresholds"]["slip_min_confidence"] == 0.85

    detail = client.get(f"/api/runs/{started['run_id']}").json()
    steps = detail["metrics"]["steps"]
    assert [s["number"] for s in steps if s["lane"] == "main"] == [str(n) for n in range(1, 12)]
    assert [s["name"] for s in steps if s["lane"] == "slip"] == ["Contract (slip lane)"]
    assert all(s["status"] == "PASSED" for s in steps) and detail["review"] == []
    assert {"AAL", "OEP 250-year", "Pricing tool"} <= {row[0] for row in detail["outcome"]}

    events = client.get(f"/api/runs/{started['run_id']}/audit").json()["events"]
    assert len(events) > 100 and {"ts", "agent", "input_ref", "decision", "confidence", "rule_or_model"} <= set(events[0])


def test_messy_run_is_worked_through_the_review_queue(client, settings, tmp_path):
    from forty8.synth.generator import generate

    sub = generate(tmp_path / "truth", profile="messy")          # same seed as the demo, for the answers
    run_id = client.post("/api/demo", json={"profile": "messy"}).json()["run_id"]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["status"] == "AWAITING_REVIEW"
    by_step = {s["step"]: s["status"] for s in detail["metrics"]["steps"]}
    assert by_step["cleaning"] == by_step["contract"] == "AWAITING_REVIEW" and by_step["schema_mapping"] == "PENDING"

    for _ in range(20):
        open_items = client.get("/api/review", params={"run_id": run_id}).json()["items"]
        if not open_items:
            break
        for raw in open_items:
            from forty8.models.common import ReviewItem
            value = answer(ReviewItem.model_validate(raw), sub)
            assert client.post(f"/api/review/{raw['id']}/correct", json={"value": value, "analyst": "alice"}).status_code == 200
        client.post(f"/api/runs/{run_id}/resume")
    done = client.get(f"/api/runs/{run_id}").json()
    assert done["run"]["status"] == "COMPLETED" and done["metrics"]["analyst_touches"] == len(done["review"]) > 10

    proposed = client.get("/api/learn", params={"status": "proposed"}).json()["mappings"]
    assert proposed
    own = client.post(f"/api/learn/{proposed[0]['id']}/approve", json={"analyst": "alice"})
    assert own.status_code == 403 and "different analyst" in own.json()["detail"]
    approved = client.post(f"/api/learn/{proposed[0]['id']}/approve", json={"analyst": "bob"}).json()
    assert approved["status"] == "approved" and approved["approved_by"] == "bob"
    assert client.post(f"/api/learn/{proposed[0]['id']}/revoke", json={"analyst": "carol"}).json()["status"] == "revoked"


def test_errors_are_reported_clearly(client):
    assert client.get("/api/runs/run_missing").status_code == 404
    assert client.get("/api/runs/run_missing/audit").status_code == 404
    assert client.post("/api/runs/run_missing/resume").status_code == 404
    assert client.post("/api/review/999/correct", json={"value": 1, "analyst": "alice"}).status_code == 404
    assert client.post("/api/demo", json={"profile": "nonsense"}).status_code == 400
    run_id = client.post("/api/demo", json={"profile": "messy"}).json()["run_id"]
    item = client.get("/api/review", params={"run_id": run_id}).json()["items"][0]
    assert client.post(f"/api/review/{item['id']}/correct", json={"value": 5, "analyst": " "}).status_code == 400
    assert client.post(f"/api/review/{item['id']}/correct", json={"value": 5, "analyst": "alice"}).status_code == 200
    again = client.post(f"/api/review/{item['id']}/correct", json={"value": 5, "analyst": "alice"})
    assert again.status_code == 400 and "already resolved" in again.json()["detail"]
