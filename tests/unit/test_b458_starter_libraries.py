"""B458 (ADR-0066 T5) — SOAR starter playbook + scenario libraries.

T5 ships the operator runbook + the starter playbook library
(config/playbooks/) + the starter scenario library
(config/purple_pete_scenarios/). This file covers the parts with a
Python surface:

  - both starter libraries parse 100% clean (the exact check
    section-01 runs)
  - every starter playbook triggers on a REAL detection rule id
  - the libraries compose: the starter scenarios, replayed through
    the starter detection rules, produce the expected coverage
    (four detected, one deliberate gap)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.security.detection import (
    DetectionEngine,
    parse_rules_from_dir,
)
from forest_soul_forge.security.playbook import (
    PlaybookEngine,
    parse_playbooks_from_dir,
)
from forest_soul_forge.security.purple_team import (
    ScenarioRunner,
    parse_scenarios_from_dir,
)

REPO = Path(__file__).resolve().parents[2]
RULES_DIR = REPO / "config" / "detection_rules"
PLAYBOOKS_DIR = REPO / "config" / "playbooks"
SCENARIOS_DIR = REPO / "config" / "purple_pete_scenarios"


# ---- library integrity ---------------------------------------------------


def test_starter_playbooks_parse_clean():
    """ADR-0066 D7 — one bad playbook halts the engine, so the
    starter library must be 100% clean."""
    assert PLAYBOOKS_DIR.exists(), "config/playbooks/ must exist"
    parsed, failed = parse_playbooks_from_dir(PLAYBOOKS_DIR)
    assert not failed, (
        "starter playbook(s) failed to parse: "
        + "; ".join(f"{p.name or '(dup)'}: {e}" for p, e in failed)
    )
    assert len(parsed) >= 3, f"expected >= 3 starter playbooks, got {len(parsed)}"


def test_starter_scenarios_parse_clean():
    assert SCENARIOS_DIR.exists(), "config/purple_pete_scenarios/ must exist"
    parsed, failed = parse_scenarios_from_dir(SCENARIOS_DIR)
    assert not failed, (
        "starter scenario(s) failed to parse: "
        + "; ".join(f"{p.name or '(dup)'}: {e}" for p, e in failed)
    )
    # ADR-0066 D6 — 3-5 starter scenarios.
    assert 3 <= len(parsed) <= 6, f"expected 3-6 starter scenarios, got {len(parsed)}"


def test_every_playbook_triggers_on_a_real_rule():
    """A playbook whose trigger names a non-existent rule id is dead
    wiring — it can never fire."""
    rules, _ = parse_rules_from_dir(RULES_DIR)
    real_rule_ids = {r.rule_id for r in rules}
    playbooks, _ = parse_playbooks_from_dir(PLAYBOOKS_DIR)
    for pb in playbooks:
        for rid in pb.trigger.detection_rule_ids:
            assert rid in real_rule_ids, (
                f"playbook {pb.playbook_id!r} triggers on {rid!r}, "
                f"which is not a real detection rule "
                f"(have: {sorted(real_rule_ids)})"
            )


def test_every_scenario_declares_a_technique():
    scenarios, _ = parse_scenarios_from_dir(SCENARIOS_DIR)
    for sc in scenarios:
        assert sc.technique.startswith("attack."), (
            f"scenario {sc.scenario_id!r}: technique {sc.technique!r} "
            f"is not an attack.* ATT&CK id"
        )


# ---- the libraries compose ----------------------------------------------


def test_starter_libraries_compose_into_the_full_loop():
    """Replay every starter scenario through the starter detection
    rules + starter playbooks. The library is tuned so four
    scenarios are detected and exactly one (process-discovery-gap)
    is a deliberate, standing coverage gap."""
    det = DetectionEngine(rules_dir=RULES_DIR)
    pb = PlaybookEngine(playbooks_dir=PLAYBOOKS_DIR)
    runner = ScenarioRunner(scenarios_dir=SCENARIOS_DIR)
    assert det.ready() and pb.ready() and runner.ready()

    results = runner.run_all(det, playbook_engine=pb)
    by_id = {r.scenario_id: r for r in results}

    # Four scenarios match a real rule.
    for sid in ("osascript-shell-spawn", "spctl-gatekeeper-disable",
                "keychain-credential-access", "reverse-shell-beacon"):
        assert by_id[sid].detected, f"{sid} should be detected"
        assert not by_id[sid].coverage_gap, f"{sid} should not be a gap"

    # The deliberate coverage gap.
    gap = by_id["process-discovery-gap"]
    assert gap.coverage_gap is True
    assert gap.detected is False

    # At least one scenario draws a playbook response (the
    # reverse-shell + gatekeeper playbooks trigger on real rules).
    assert any(r.responded for r in results), (
        "no starter scenario drew a playbook response"
    )
