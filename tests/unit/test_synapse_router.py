"""Tests for the read-only /synapse API router (daemon/routers/synapse.py).

The router is the public read surface over Forest's synaptic layer (ADR-0095):
it must faithfully report trust + provenance, expose the chain-integrity check,
surface quarantine candidates — and never offer a mutation path (trust→capability
stays human-gated; the router has no write endpoints). These tests mount the
router on a bare app with a seeded graph so they pin the HTTP contract without a
full daemon birth.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forest_soul_forge.daemon.routers import synapse as synapse_router
from forest_soul_forge.synapse import TrustGraph


def _app(graph) -> TestClient:
    app = FastAPI()
    app.include_router(synapse_router.router)
    app.state.trust_graph = graph
    return TestClient(app)


def _seeded() -> TrustGraph:
    g = TrustGraph()
    # claude: strong at regulatory, with provenance pointers
    g.record("claude", "regulatory", True, evidence="audit:1")
    g.record("claude", "regulatory", True, evidence="audit:2")
    g.record("claude", "regulatory", False, evidence="audit:3")
    # rotten: confidently bad → a quarantine candidate
    for i in range(8):
        g.record("rotten", "regulatory", False, evidence=f"audit:{10 + i}")
    return g


def test_trust_lists_all_scores_ranked_desc():
    c = _app(_seeded())
    body = c.get("/synapse/trust").json()
    assert body["count"] == 2
    trusts = [s["trust"] for s in body["scores"]]
    assert trusts == sorted(trusts, reverse=True)        # ranked best-first
    claude = next(s for s in body["scores"] if s["node"] == "claude")
    assert claude["problem_class"] == "regulatory"
    assert claude["observations"] == 3.0
    assert 0.0 <= claude["interval"][0] <= claude["trust"] <= claude["interval"][1] <= 1.0


def test_trust_single_pair_and_filters():
    c = _app(_seeded())
    one = c.get("/synapse/trust", params={"node": "claude", "problem_class": "regulatory"}).json()
    assert one["trust"]["node"] == "claude" and one["trust"]["observations"] == 3.0
    only = c.get("/synapse/trust", params={"node": "rotten"}).json()
    assert only["count"] == 1 and only["scores"][0]["node"] == "rotten"


def test_why_surfaces_audit_cross_links_in_order():
    c = _app(_seeded())
    body = c.get("/synapse/why", params={"node": "claude", "problem_class": "regulatory"}).json()
    assert body["n"] == 3
    assert [o["evidence"] for o in body["outcomes"]] == ["audit:1", "audit:2", "audit:3"]
    assert [o["success"] for o in body["outcomes"]] == [True, True, False]


def test_quarantined_surfaces_confident_low_trust_only():
    c = _app(_seeded())
    body = c.get("/synapse/quarantined").json()
    nodes = [s["node"] for s in body["quarantine_candidates"]]
    assert nodes == ["rotten"]                            # claude is fine; rotten is confidently bad


def test_bounties_rank_by_uncertainty():
    c = _app(_seeded())
    body = c.get("/synapse/bounties").json()
    assert body["count"] >= 1
    us = [b["uncertainty"] for b in body["bounties"]]
    assert us == sorted(us, reverse=True)                 # widest uncertainty first
    assert all({"node", "problem_class", "uncertainty"} <= b.keys() for b in body["bounties"])


def test_verify_reports_chain_integrity():
    c = _app(_seeded())
    body = c.get("/synapse/verify").json()
    assert body["ok"] is True and body["reason"] is None
    assert body["outcomes"] == 11                         # 3 claude + 8 rotten


def test_nodes_lists_sorted_nodes_and_classes():
    c = _app(_seeded())
    body = c.get("/synapse/nodes").json()
    assert body["nodes"] == ["claude", "rotten"]
    assert body["problem_classes"] == ["regulatory"]


def test_route_recommends_and_is_reproducible_with_seed():
    c = _app(_seeded())
    a = c.get("/synapse/route", params={"problem_class": "regulatory", "seed": 7}).json()
    b = c.get("/synapse/route", params={"problem_class": "regulatory", "seed": 7}).json()
    assert a == b                                         # same seed → identical ranking
    assert {r["node"] for r in a["ranking"]} == {"claude", "rotten"}
    assert all({"node", "sample", "trust", "observations"} <= r.keys() for r in a["ranking"])
    # claude (mean 0.6, n=3) should be exploited over rotten (confidently bad) across seeds
    wins = sum(
        c.get("/synapse/route", params={"problem_class": "regulatory", "seed": s})
         .json()["recommended"] == "claude"
        for s in range(200)
    )
    assert wins > 150                                     # exploit the proven node most of the time


def test_route_respects_explicit_candidates():
    c = _app(_seeded())
    body = c.get("/synapse/route", params={
        "problem_class": "regulatory", "candidates": "rotten", "seed": 1}).json()
    assert body["candidates"] == ["rotten"] and body["recommended"] == "rotten"


def test_route_empty_for_unknown_problem_class():
    c = _app(_seeded())
    body = c.get("/synapse/route", params={"problem_class": "no_such_class"}).json()
    assert body["recommended"] is None and body["ranking"] == []


def test_returns_503_when_synaptic_layer_unwired():
    c = _app(None)                                        # app.state.trust_graph = None
    for path in ("/synapse/trust", "/synapse/verify", "/synapse/nodes"):
        assert c.get(path).status_code == 503
    assert c.get("/synapse/route", params={"problem_class": "x"}).status_code == 503
