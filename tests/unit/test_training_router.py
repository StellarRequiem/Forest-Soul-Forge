"""Tests for the /training Operator Console backend (ADR-0096)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forest_soul_forge.daemon.routers import training as training_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(training_router.router)
    return TestClient(app)


def test_get_tasks_lists_the_tiered_ladder():
    body = _client().get("/training/tasks").json()
    assert body["count"] >= 6
    assert sorted({t["tier"] for t in body["tasks"]}) == [0, 1, 2, 3, 4]
    assert all(t["side_effects"] == "read_only" for t in body["tasks"])
    assert all(t["steps"] and t["steps"][0]["tool"] for t in body["tasks"])


def test_post_run_executes_the_ladder_and_returns_a_green_report():
    r = _client().post("/training/run")
    assert r.status_code == 200
    d = r.json()
    assert d["schema"] == "fsf.training_report.v1"
    assert d["passed"] == d["total"] and d["total"] >= 6      # green ladder
    assert d["audit_chain_ok"] is True and d["trust_graph_ok"] is True
    assert set(d["by_tier"]) == {"0", "1", "2", "3", "4"}
