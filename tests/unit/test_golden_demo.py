"""Smoke test for the Golden Demo (demo/golden/golden_demo.py).

The demo is a portfolio centerpiece + the canonical "evaluate FSF in 60s" path,
so it must not silently rot when the AuditChain or signing API changes. This
runs the whole demo end-to-end (real chain, real ed25519, in a temp dir) and
asserts it exits clean — which only happens if every phase, including both
tamper-detection paths, behaves as designed.
"""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("cryptography")   # ed25519 backend ([daemon] extra)
pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]


def _load_demo():
    spec = importlib.util.spec_from_file_location(
        "golden_demo", ROOT / "demo" / "golden" / "golden_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_golden_demo_runs_clean(capsys):
    mod = _load_demo()
    assert mod.main() == 0
    out = capsys.readouterr().out
    # the load-bearing beats must actually appear
    assert "chain verified" in out                      # phase 5 clean verify
    assert "entry_hash mismatch" in out                 # lazy tamper → hash chain
    assert "signature verification failed" in out       # expert tamper → ed25519
    assert "provenance" in out.lower()


def test_acting_genre_is_real_config():
    # The governance gate must be grounded in config/genres.yaml, not invented.
    mod = _load_demo()
    name, risk = mod.load_acting_genre()
    assert isinstance(name, str) and name
    assert risk.get("max_side_effects") not in (None, "read_only")
