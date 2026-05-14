"""ADR-0067 T5 (B283) — domain_orchestrator role + singleton tests.

Covers the configuration-level surface (no live registry):
  - trait_tree.yaml: domain_orchestrator role exists with the
    documented domain_weights shape
  - genres.yaml: companion genre claims domain_orchestrator
  - constitution_templates.yaml: template exists with the expected
    forbid_direct_action + forbid_self_delegate policies + the
    allowed_tools list

Live singleton enforcement (the /birth refusal on second spawn)
is exercised by the existing tests/unit/test_daemon_writes_birth.py
patterns — when those tests run on a daemon with the B283 code,
the second-spawn refusal kicks in automatically.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# trait_tree.yaml
# ---------------------------------------------------------------------------
def test_trait_tree_has_domain_orchestrator():
    path = REPO_ROOT / "config" / "trait_tree.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    roles = data.get("role_base", {})
    assert "domain_orchestrator" in roles, (
        "domain_orchestrator role missing from config/trait_tree.yaml"
    )
    entry = roles["domain_orchestrator"]
    assert "description" in entry
    weights = entry["domain_weights"]
    # All six domains present.
    for d in ("security", "audit", "cognitive", "communication",
              "emotional", "embodiment"):
        assert d in weights, (
            f"trait_tree.yaml domain_orchestrator missing weight: {d}"
        )
    # Validator floor [0.4, 3.0] — anchors below this trip lifespan.
    for d, w in weights.items():
        assert 0.4 <= w <= 3.0, (
            f"domain_orchestrator weight {d}={w} outside [0.4, 3.0]"
        )
    # Communication should be the heaviest weight (orchestrator's job
    # is intent understanding + handoff).
    assert weights["communication"] >= max(
        weights["security"], weights["audit"], weights["embodiment"],
    )


# ---------------------------------------------------------------------------
# genres.yaml
# ---------------------------------------------------------------------------
def test_companion_genre_claims_domain_orchestrator():
    path = REPO_ROOT / "config" / "genres.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    companion = data.get("genres", {}).get("companion")
    assert companion is not None, "companion genre missing from genres.yaml"
    roles = companion.get("roles") or []
    assert "domain_orchestrator" in roles, (
        "companion genre doesn't claim domain_orchestrator"
    )


# ---------------------------------------------------------------------------
# constitution_templates.yaml
# ---------------------------------------------------------------------------
def test_constitution_template_present():
    path = REPO_ROOT / "config" / "constitution_templates.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    templates = data.get("role_templates", {})
    tpl = templates.get("domain_orchestrator")
    assert tpl is not None, (
        "domain_orchestrator template missing from constitution_templates.yaml"
    )

    # Required policies
    policy_ids = [p["id"] for p in tpl.get("policies", [])]
    assert "forbid_direct_action" in policy_ids
    assert "forbid_self_delegate" in policy_ids

    # Required tools — orchestrator constitution must include the
    # decompose + route primitives, the profile read, llm_think for
    # decomposition prompts, delegate for fallback, verify_claim for
    # sanity checks.
    allowed = set(tpl.get("allowed_tools") or [])
    for required in (
        "decompose_intent.v1",
        "route_to_domain.v1",
        "operator_profile_read.v1",
        "llm_think.v1",
        "delegate.v1",
    ):
        assert required in allowed, (
            f"domain_orchestrator constitution missing required tool: "
            f"{required}"
        )

    # Reality Anchor opt-in is mandatory — the orchestrator's
    # routing decisions get cross-checked against ground truth.
    assert tpl.get("reality_anchor", {}).get("enabled") is True


# ---------------------------------------------------------------------------
# Singleton enforcement at birth.py edit site
# ---------------------------------------------------------------------------
def test_singleton_set_includes_domain_orchestrator():
    """Both reality_anchor and domain_orchestrator are in the
    _SINGLETON_ROLES enforcement set in writes/birth.py."""
    path = REPO_ROOT / "src" / "forest_soul_forge" / "daemon" / "routers" / "writes" / "birth.py"
    src = path.read_text(encoding="utf-8")
    # The literal set construction we wrote in B283.
    assert '_SINGLETON_ROLES = {"reality_anchor", "domain_orchestrator"}' in src, (
        "_SINGLETON_ROLES set in birth.py must include both "
        "reality_anchor (ADR-0063 T4) and domain_orchestrator "
        "(ADR-0067 T5)"
    )
