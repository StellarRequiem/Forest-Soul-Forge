"""Tests for the chronological-canon generator + drift gate (dev-tools/state_canon.py).

The gate guards FSF's documented counts against disk. These tests guard the gate —
especially the TIER SEPARATION: content facts are hard-gated, while VCS provenance
(head sha / commit count) and volatile runtime facts (agent counts / audit length)
are informational only. If a commit-advancing or host-local field ever leaks back
into the gated `repo` tier, CI would cry wolf on every PR — these tests fail first.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "state_canon", ROOT / "dev-tools" / "state_canon.py")
state_canon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state_canon)


@pytest.fixture(scope="module")
def m():
    return state_canon.measure()


def test_measure_has_all_five_tiers(m):
    assert set(m) == {"repo", "provenance", "runtime", "declared", "dynamic"}


def test_repo_is_exactly_the_gated_content_facts(m):
    # The hard-gated set is content-derived and commit-agnostic. Adding/removing a
    # field here is a deliberate decision — pin it so leaks are caught.
    assert set(m["repo"]) == {
        "python_loc", "adr_files", "adr_unique", "test_files",
        "test_functions", "builtin_tools", "pyproject_version", "latest_tag",
    }


def test_vcs_provenance_is_not_gated(m):
    # head_sha / commits_main advance every commit; a PR merge ref differs from the
    # branch HEAD. They MUST NOT be in the gated repo tier or CI false-fails on every PR.
    assert set(m["provenance"]) == {"head_sha", "commits_main"}
    assert "head_sha" not in m["repo"]
    assert "commits_main" not in m["repo"]


def test_runtime_volatile_facts_are_not_gated(m):
    # registry DB is gitignored (absent in CI); audit chain grows continuously.
    assert "agents_total" not in m["repo"]
    assert "audit_chain_entries" not in m["repo"]


def test_declared_schema_is_surfaced_not_gated(m):
    # Schema version isn't static-measurable (PRAGMA user_version == 0); it's declared.
    assert m["declared"]["schema_version"].startswith("v")
    assert "schema_version" not in m["repo"]


def test_counts_are_nonnegative_ints(m):
    for k in ("python_loc", "adr_files", "adr_unique", "test_files",
              "test_functions", "builtin_tools"):
        assert isinstance(m["repo"][k], int) and m["repo"][k] >= 0


def test_repo_has_real_content(m):
    # Sanity floors — the repo is large; these guard a measurement that silently
    # returns 0 (e.g. a broken path) rather than the true count.
    assert m["repo"]["python_loc"] > 10_000
    assert m["repo"]["test_functions"] > 1_000
    assert m["repo"]["builtin_tools"] > 10


def test_render_block_is_fenced_and_carries_measured_values(m):
    block = render = state_canon.render_block(m, "2026-01-01 00:00Z")
    assert state_canon.BEGIN in block and state_canon.END in block
    # the LoC number must appear (thousands-formatted) — proves render reads measure()
    assert f"{m['repo']['python_loc']:,}" in render
    # provenance is labelled informational, present but not a gated row
    assert "informational, not gated" in render


def test_readme_checks_keys_are_measured_repo_facts(m):
    # The README gate may only enforce fields we actually measure — else KeyError.
    assert set(state_canon.README_CHECKS) <= set(m["repo"])


def test_readme_rows_all_parse(m):
    # Every README_CHECKS regex must still match its row in the live README; a
    # reworded row that silently stops matching would drop a field from the gate.
    rows = state_canon.check_readme(m["repo"])
    unparsed = [k for k, _claimed, _disk, status in rows if status == "unparsed"]
    assert not unparsed, f"README rows no longer matched (update regex): {unparsed}"


def test_readme_loc_tolerance_vs_exact_fields():
    # LoC is tolerance-gated (within 1%) so a small src change doesn't force a
    # README bump; egregious drift (the reviewer quoted a figure 41% off) still
    # fails. Discrete counts stay exact — a stale one is a real oversight.
    assert state_canon._readme_ok("python_loc", 101_000, 101_655)      # 655 off, <1% -> ok
    assert not state_canon._readme_ok("python_loc", 60_000, 101_655)   # 41% off -> drift
    assert state_canon._readme_ok("adr_files", 89, 89)                 # exact -> ok
    assert not state_canon._readme_ok("adr_files", 88, 89)             # off by one -> drift
