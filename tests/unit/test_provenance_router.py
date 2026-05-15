"""ADR-0072 T5 (B330) — /provenance/* router tests.

Coverage:
  /provenance/active:
    - returns precedence ladder with the four canonical tiers
    - returns empty preferences/rules when files don't exist
      (load_* returns empty config + soft error)
    - active rules bucket holds status='active' rules
    - pending bucket holds status='pending_activation'
    - refused bucket holds status='refused' rules from the
      pending_activation list
    - load errors land in the response 'errors' field

  /provenance/handoffs:
    - returns flat lists of default_skill_per_capability +
      cascade_rules
    - empty handoffs.yaml returns empty lists
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
import yaml

from forest_soul_forge.daemon.routers.provenance import (
    provenance_active,
    provenance_handoffs,
)
from forest_soul_forge.core.behavior_provenance import (
    LEARNED_RULES_ENV,
    PREFERENCES_ENV,
)
from forest_soul_forge.core.routing_engine import ENV_VAR as HANDOFFS_ENV


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# /provenance/active
# ---------------------------------------------------------------------------


def test_active_returns_precedence_ladder(monkeypatch, tmp_path):
    # Point both env vars at non-existent paths → empty configs.
    monkeypatch.setenv(PREFERENCES_ENV, str(tmp_path / "nope.yaml"))
    monkeypatch.setenv(LEARNED_RULES_ENV, str(tmp_path / "nope2.yaml"))
    out = _run(provenance_active())
    tiers = [p["name"] for p in out["precedence"]]
    assert tiers == [
        "hardcoded_handoff",
        "constitutional",
        "preference",
        "learned",
    ]
    # Tier numbers descending (1000 > 800 > 400 > 100).
    assert [p["tier"] for p in out["precedence"]] == [1000, 800, 400, 100]


def test_active_missing_files_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv(PREFERENCES_ENV, str(tmp_path / "missing_pref.yaml"))
    monkeypatch.setenv(LEARNED_RULES_ENV, str(tmp_path / "missing_rules.yaml"))
    out = _run(provenance_active())
    assert out["preferences"] == []
    assert out["learned_rules"]["active"] == []
    assert out["learned_rules"]["pending_activation"] == []
    assert out["learned_rules"]["refused"] == []
    # Soft errors from missing files surface.
    assert any("preferences" in e or "learned" in e for e in out["errors"])


def test_active_buckets_rules_by_status(monkeypatch, tmp_path):
    prefs_path = tmp_path / "prefs.yaml"
    rules_path = tmp_path / "rules.yaml"
    prefs_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "preferences": [{
            "id": "p1", "statement": "prefer d7",
            "weight": 0.7, "domain": "d7",
        }],
    }))
    rules_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "pending_activation": [
            {
                "id": "r_pending", "statement": "x", "weight": 0.5,
                "domain": "d7", "proposer_agent_dna": "d",
                "status": "pending_activation",
            },
            {
                "id": "r_refused", "statement": "y", "weight": 0.5,
                "domain": "d10", "proposer_agent_dna": "d",
                "status": "refused", "verification_verdict": "contradicted",
                "verification_reason": "RA contradicted via fact_id 'f1'",
            },
        ],
        "active": [{
            "id": "r_active", "statement": "z", "weight": 0.8,
            "domain": "d3", "proposer_agent_dna": "d",
            "status": "active",
        }],
    }))
    monkeypatch.setenv(PREFERENCES_ENV, str(prefs_path))
    monkeypatch.setenv(LEARNED_RULES_ENV, str(rules_path))
    out = _run(provenance_active())

    assert len(out["preferences"]) == 1
    assert out["preferences"][0]["id"] == "p1"

    active_ids = [r["id"] for r in out["learned_rules"]["active"]]
    pending_ids = [r["id"] for r in out["learned_rules"]["pending_activation"]]
    refused_ids = [r["id"] for r in out["learned_rules"]["refused"]]
    assert active_ids == ["r_active"]
    assert pending_ids == ["r_pending"]
    assert refused_ids == ["r_refused"]
    # Refused rule carries verification metadata.
    refused = out["learned_rules"]["refused"][0]
    assert refused["verification_verdict"] == "contradicted"
    assert "fact_id" in refused["verification_reason"]


def test_active_skips_pending_buckets_that_are_other_statuses(monkeypatch, tmp_path):
    """A rule with status='active' that someone wrote into the
    pending_activation list should NOT show up in the pending
    bucket — the API filters by actual status field."""
    rules_path = tmp_path / "rules.yaml"
    rules_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "pending_activation": [{
            "id": "weird", "statement": "x", "weight": 0.5,
            "domain": "d7", "proposer_agent_dna": "d",
            "status": "active",  # this rule has 'active' status but is in pending list
        }],
        "active": [],
    }))
    monkeypatch.setenv(LEARNED_RULES_ENV, str(rules_path))
    monkeypatch.setenv(PREFERENCES_ENV, str(tmp_path / "nope.yaml"))
    out = _run(provenance_active())
    assert out["learned_rules"]["pending_activation"] == []
    assert out["learned_rules"]["refused"] == []


# ---------------------------------------------------------------------------
# /provenance/handoffs
# ---------------------------------------------------------------------------


def test_handoffs_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv(HANDOFFS_ENV, str(tmp_path / "missing.yaml"))
    out = _run(provenance_handoffs())
    assert out["default_skill_per_capability"] == []
    assert out["cascade_rules"] == []


def test_handoffs_returns_flat_lists(monkeypatch, tmp_path):
    handoffs_path = tmp_path / "handoffs.yaml"
    handoffs_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "default_skill_per_capability": [
            {"domain": "d4", "capability": "review",
             "skill_name": "pr_review", "skill_version": "1"},
        ],
        "cascade_rules": [
            {"source_domain": "d4", "source_capability": "review",
             "target_domain": "d8", "target_capability": "scan",
             "reason": "every PR triggers compliance pass"},
        ],
    }))
    monkeypatch.setenv(HANDOFFS_ENV, str(handoffs_path))
    out = _run(provenance_handoffs())
    assert len(out["default_skill_per_capability"]) == 1
    d = out["default_skill_per_capability"][0]
    assert d["domain"] == "d4"
    assert d["skill_name"] == "pr_review"
    assert d["skill_version"] == "1"
    assert len(out["cascade_rules"]) == 1
    c = out["cascade_rules"][0]
    assert c["source_domain"] == "d4"
    assert c["target_domain"] == "d8"
    assert "compliance pass" in c["reason"]
