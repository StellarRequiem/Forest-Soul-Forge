"""Tests for Forest's synaptic layer (synapse/trust_graph.py).

Pins the four load-bearing properties: provenance-first (tamper-evident replay),
contextual trust, calibrated-and-honest posteriors, and governed quarantine.
"""
import json
import random

import pytest

from forest_soul_forge.synapse import Outcome, TrustGraph, TrustScore


def test_trust_rises_with_success_falls_with_failure():
    g = TrustGraph()
    base = g.trust("a", "fx").mean
    for _ in range(10):
        g.record("a", "fx", True)
    assert g.trust("a", "fx").mean > base
    for _ in range(30):
        g.record("a", "fx", False)
    assert g.trust("a", "fx").mean < 0.5


def test_trust_is_contextual_per_problem_class():
    g = TrustGraph()
    for _ in range(20):
        g.record("quant", "regulatory_timing", False)   # bad at this
        g.record("quant", "code_review", True)           # good at that
    reg = g.trust("quant", "regulatory_timing").mean
    code = g.trust("quant", "code_review").mean
    assert reg < 0.2 and code > 0.8, (reg, code)


def test_posterior_is_honest_about_ignorance():
    g = TrustGraph()
    g.record("a", "fx", True); g.record("a", "fx", True)      # 2 obs
    few = g.trust("a", "fx")
    for _ in range(200):
        g.record("b", "fx", True)
    many = g.trust("b", "fx")
    # same direction of belief, but the well-tested node is far sharper
    assert many.stdev < few.stdev
    assert few.n == 2 and many.n == 200
    lo, hi = few.interval()
    assert lo < few.mean < hi  # the interval brackets the mean


def test_thompson_routing_is_deterministic_with_seed_and_exploits_winners():
    g = TrustGraph()
    for _ in range(50):
        g.record("strong", "fx", True)
    for _ in range(50):
        g.record("weak", "fx", False)
    g.trust("fresh", "fx")  # untested -> wide posterior
    # deterministic given a seeded rng
    r1 = g.rank(["strong", "weak", "fresh"], "fx", rng=random.Random(7))
    r2 = g.rank(["strong", "weak", "fresh"], "fx", rng=random.Random(7))
    assert r1 == r2
    # over many seeds, 'strong' wins far more than 'weak'
    wins = {"strong": 0, "weak": 0, "fresh": 0}
    for s in range(400):
        wins[g.best(["strong", "weak", "fresh"], "fx", rng=random.Random(s))] += 1
    assert wins["strong"] > wins["weak"]
    assert wins["fresh"] > 0      # exploration still happens for the untested node


def test_quarantine_needs_confidence_not_just_a_low_mean():
    g = TrustGraph()
    g.record("noisy", "fx", False)   # 1 failure — low mean, but no confidence
    assert g.quarantined(threshold=0.4, min_n=5) == []
    for _ in range(20):
        g.record("rotten", "fx", False)
    q = g.quarantined(threshold=0.4, min_n=5)
    assert [s.node for s in q] == ["rotten"]


def test_why_surfaces_the_provenance():
    g = TrustGraph()
    g.record("a", "fx", True, evidence="audit:101")
    g.record("a", "fx", False, evidence="audit:102")
    g.record("a", "code", True, evidence="test:7")
    prov = g.why("a", "fx")
    assert [o.evidence for o in prov] == ["audit:101", "audit:102"]
    assert all(isinstance(o, Outcome) for o in prov)


def test_ledger_is_hash_chained_and_tamper_evident():
    g = TrustGraph()
    for i in range(5):
        g.record("a", "fx", i % 2 == 0)
    ok, reason = g.verify()
    assert ok and reason is None
    # tamper: flip a past outcome's success in the in-memory ledger
    bad = g._ledger
    e = bad[2]
    bad[2] = Outcome(seq=e.seq, node=e.node, problem_class=e.problem_class,
                     success=not e.success, weight=e.weight, evidence=e.evidence,
                     prev_hash=e.prev_hash, entry_hash=e.entry_hash)
    ok2, reason2 = g.verify()
    assert not ok2 and "entry_hash mismatch" in reason2


def test_save_load_roundtrip_reproduces_trust_and_verifies(tmp_path):
    g = TrustGraph()
    rng = random.Random(1)
    for _ in range(60):
        node = rng.choice(["a", "b", "c"])
        pc = rng.choice(["fx", "code"])
        g.record(node, pc, rng.random() < 0.7, evidence=f"e{rng.randint(0,9)}")
    p = tmp_path / "trust.jsonl"
    g.save(p)
    g2 = TrustGraph.load(p)
    assert g2.verify()[0]
    for node in g.nodes():
        for pc in g.problem_classes():
            assert abs(g.trust(node, pc).mean - g2.trust(node, pc).mean) < 1e-9


def test_replay_detects_a_forged_ledger(tmp_path):
    g = TrustGraph()
    for _ in range(4):
        g.record("a", "fx", True)
    p = tmp_path / "t.jsonl"
    g.save(p)
    lines = p.read_text().splitlines()
    obj = json.loads(lines[1]); obj["success"] = False  # forge an outcome
    lines[1] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="tamper"):
        TrustGraph.load(p)


def test_record_rejects_bad_input():
    g = TrustGraph()
    for bad in [("", "fx"), ("a", "")]:
        with pytest.raises(ValueError):
            g.record(*bad, True)
    with pytest.raises(ValueError):
        g.record("a", "fx", True, weight=0)
