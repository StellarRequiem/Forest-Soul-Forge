"""ADR-0072 T1 (B290) — behavior provenance substrate tests.

Covers:
  - PRECEDENCE table + resolve_precedence
  - load_preferences happy path / missing file / schema mismatch /
    per-entry errors
  - load_learned_rules happy path / pending vs active sections /
    bad status / unknown status
  - save round-trips
  - compute_behavior_change_delta: added / modified / removed
  - audit_chain event types registered
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.core.behavior_provenance import (
    LEARNED_RULES_ENV,
    PREFERENCES_ENV,
    PRECEDENCE,
    BehaviorProvenanceError,
    LearnedRule,
    LearnedRulesConfig,
    Preference,
    PreferencesConfig,
    compute_behavior_change_delta,
    load_learned_rules,
    load_preferences,
    resolve_precedence,
    save_learned_rules,
    save_preferences,
)


# ---------------------------------------------------------------------------
# PRECEDENCE
# ---------------------------------------------------------------------------
def test_precedence_ordering():
    """Higher = wins. ADR-0072 D1 hierarchy."""
    assert PRECEDENCE["hardcoded_handoff"] > PRECEDENCE["constitutional"]
    assert PRECEDENCE["constitutional"] > PRECEDENCE["preference"]
    assert PRECEDENCE["preference"] > PRECEDENCE["learned"]


def test_resolve_precedence_hardcoded_wins_over_learned():
    assert resolve_precedence("hardcoded_handoff", "learned") == "hardcoded_handoff"
    assert resolve_precedence("learned", "hardcoded_handoff") == "hardcoded_handoff"


def test_resolve_precedence_unknown_layer_raises():
    with pytest.raises(ValueError, match="unknown layer"):
        resolve_precedence("hardcoded_handoff", "made_up_layer")


# ---------------------------------------------------------------------------
# load_preferences
# ---------------------------------------------------------------------------
def test_load_preferences_missing_file_is_soft(tmp_path):
    cfg, errors = load_preferences(tmp_path / "nope.yaml")
    assert cfg.preferences == ()
    assert any("not found" in e for e in errors)


def test_load_preferences_schema_mismatch_raises(tmp_path):
    p = tmp_path / "prefs.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 999, "preferences": []}))
    with pytest.raises(BehaviorProvenanceError, match="schema_version"):
        load_preferences(p)


def test_load_preferences_happy_path(tmp_path):
    p = tmp_path / "prefs.yaml"
    p.write_text(yaml.safe_dump({
        "schema_version": 1,
        "preferences": [
            {"id": "test.pref", "statement": "test pref",
             "weight": 0.7, "domain": "orchestrator"},
        ],
    }))
    cfg, errors = load_preferences(p)
    assert errors == []
    assert len(cfg.preferences) == 1
    assert cfg.preferences[0].weight == 0.7


def test_load_preferences_bad_weight_is_soft(tmp_path):
    p = tmp_path / "prefs.yaml"
    p.write_text(yaml.safe_dump({
        "schema_version": 1,
        "preferences": [
            {"id": "x", "statement": "x", "weight": 5.0, "domain": "y"},
        ],
    }))
    cfg, errors = load_preferences(p)
    assert cfg.preferences == ()  # rejected entry
    assert any("weight" in e for e in errors)


def test_load_preferences_duplicate_id_first_kept(tmp_path):
    p = tmp_path / "prefs.yaml"
    p.write_text(yaml.safe_dump({
        "schema_version": 1,
        "preferences": [
            {"id": "dup", "statement": "first", "weight": 0.5,
             "domain": "x"},
            {"id": "dup", "statement": "second", "weight": 0.3,
             "domain": "x"},
        ],
    }))
    cfg, errors = load_preferences(p)
    assert len(cfg.preferences) == 1
    assert cfg.preferences[0].statement == "first"
    assert any("duplicate" in e for e in errors)


# ---------------------------------------------------------------------------
# load_learned_rules
# ---------------------------------------------------------------------------
def test_load_learned_rules_happy_path(tmp_path):
    p = tmp_path / "learned.yaml"
    p.write_text(yaml.safe_dump({
        "schema_version": 1,
        "pending_activation": [
            {"id": "rule_a", "statement": "a", "weight": 0.5,
             "domain": "orchestrator",
             "proposer_agent_dna": "dna_abc",
             "status": "pending_activation"},
        ],
        "active": [
            {"id": "rule_b", "statement": "b", "weight": 0.6,
             "domain": "orchestrator",
             "proposer_agent_dna": "dna_xyz",
             "status": "active"},
        ],
    }))
    cfg, errors = load_learned_rules(p)
    assert errors == []
    assert len(cfg.pending_activation) == 1
    assert len(cfg.active) == 1
    assert cfg.pending_activation[0].id == "rule_a"
    assert cfg.active[0].id == "rule_b"


def test_load_learned_rules_bad_status_dropped(tmp_path):
    p = tmp_path / "learned.yaml"
    p.write_text(yaml.safe_dump({
        "schema_version": 1,
        "pending_activation": [],
        "active": [
            {"id": "x", "statement": "x", "weight": 0.5,
             "domain": "y", "proposer_agent_dna": "z",
             "status": "WEIRD_STATUS"},
        ],
    }))
    cfg, errors = load_learned_rules(p)
    assert cfg.active == ()
    assert any("status" in e for e in errors)


def test_load_learned_rules_missing_file_is_soft(tmp_path):
    cfg, errors = load_learned_rules(tmp_path / "nope.yaml")
    assert cfg.pending_activation == ()
    assert cfg.active == ()
    assert any("not found" in e for e in errors)


# ---------------------------------------------------------------------------
# save round-trips
# ---------------------------------------------------------------------------
def test_save_preferences_round_trips(tmp_path):
    cfg = PreferencesConfig(
        schema_version=1,
        preferences=(
            Preference(
                id="t.pref", statement="test", weight=0.8,
                domain="orchestrator",
                created_at="2026-05-14T00:00:00Z",
                updated_at="2026-05-14T00:00:00Z",
            ),
        ),
    )
    path = tmp_path / "prefs.yaml"
    save_preferences(cfg, path)
    loaded, errors = load_preferences(path)
    assert errors == []
    assert loaded.preferences[0].id == "t.pref"
    assert loaded.preferences[0].weight == 0.8


def test_save_learned_rules_round_trips(tmp_path):
    cfg = LearnedRulesConfig(
        schema_version=1,
        pending_activation=(
            LearnedRule(
                id="r1", statement="r1", weight=0.3,
                domain="x", proposer_agent_dna="dna",
                created_at="2026-05-14T00:00:00Z",
                status="pending_activation",
            ),
        ),
        active=(),
    )
    path = tmp_path / "learned.yaml"
    save_learned_rules(cfg, path)
    loaded, errors = load_learned_rules(path)
    assert errors == []
    assert len(loaded.pending_activation) == 1


# ---------------------------------------------------------------------------
# compute_behavior_change_delta
# ---------------------------------------------------------------------------
def test_delta_detects_added(tmp_path):
    before = PreferencesConfig(schema_version=1, preferences=())
    after = PreferencesConfig(
        schema_version=1,
        preferences=(
            Preference(
                id="new", statement="new", weight=0.5,
                domain="x",
                created_at="2026-05-14T00:00:00Z",
                updated_at="2026-05-14T00:00:00Z",
            ),
        ),
    )
    delta = compute_behavior_change_delta(before, after)
    assert len(delta["added"]) == 1
    assert delta["added"][0]["id"] == "new"
    assert delta["modified"] == []
    assert delta["removed"] == []


def test_delta_detects_modified(tmp_path):
    before = PreferencesConfig(
        schema_version=1,
        preferences=(
            Preference(
                id="x", statement="old", weight=0.3, domain="y",
                created_at="t", updated_at="t",
            ),
        ),
    )
    after = PreferencesConfig(
        schema_version=1,
        preferences=(
            Preference(
                id="x", statement="new", weight=0.8, domain="y",
                created_at="t", updated_at="t",
            ),
        ),
    )
    delta = compute_behavior_change_delta(before, after)
    assert len(delta["modified"]) == 1
    assert delta["modified"][0]["id"] == "x"
    assert delta["modified"][0]["before"]["weight"] == 0.3
    assert delta["modified"][0]["after"]["weight"] == 0.8


def test_delta_detects_removed():
    before = PreferencesConfig(
        schema_version=1,
        preferences=(
            Preference(
                id="gone", statement="gone", weight=0.5, domain="x",
                created_at="t", updated_at="t",
            ),
        ),
    )
    after = PreferencesConfig(schema_version=1, preferences=())
    delta = compute_behavior_change_delta(before, after)
    assert len(delta["removed"]) == 1
    assert delta["removed"][0]["id"] == "gone"


# ---------------------------------------------------------------------------
# Audit chain event types registered
# ---------------------------------------------------------------------------
def test_audit_chain_event_types_registered():
    """ADR-0072 emits three event types; verifier must accept them."""
    for et in ("behavior_change", "learned_rule_activated",
               "learned_rule_refused"):
        assert et in KNOWN_EVENT_TYPES, (
            f"audit_chain.py KNOWN_EVENT_TYPES missing {et}"
        )
