"""Smoke test for the synaptic-layer demo (demo/synapse/synapse_demo.py).

The demo is the legible proof of `synapse.TrustGraph`; it must not rot when the
engine changes. Runs it end-to-end (deterministic, seeded) and asserts the
load-bearing beats actually fire.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_synapse_demo_runs_and_learns(capsys):
    spec = importlib.util.spec_from_file_location(
        "synapse_demo", ROOT / "demo" / "synapse" / "synapse_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "learned trust" in out
    assert "matches ground truth" in out          # the mesh discovered the truth
    assert "QUARANTINE CANDIDATE" in out           # a collapsed node is isolated
    assert "ledger verified" in out
    assert "tamper" in out.lower()                 # forgery is caught
