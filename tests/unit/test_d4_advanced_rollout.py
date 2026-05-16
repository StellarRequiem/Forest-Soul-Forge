"""ADR-0077 (B331-B333) — D4 advanced rollout role definition tests.

Coverage:
  trait_tree.yaml:
    - test_author / migration_pilot / release_gatekeeper all parse
    - each has 6 expected domain_weight keys
    - calibration sanity: weights within [0, 3]
    - release_gatekeeper has the highest audit weight in the system
      (matches reality_anchor's ceiling)

  genres.yaml:
    - test_author claimed by 'researcher' exactly once
    - migration_pilot claimed by 'guardian' exactly once
    - release_gatekeeper claimed by 'guardian' exactly once
    - no double-claim against any other genre
    - genre invariant (every trait-engine role claimed) still holds

  constitution_templates.yaml:
    - each role has policies + risk_thresholds + out_of_scope blocks
    - test_author has forbid_production_code_edit AND forbid_self_test_deletion
    - migration_pilot has require_dry_run_before_apply +
      require_human_approval_for_apply
    - release_gatekeeper has forbid_release_action
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.genre_engine import (
    load_genres,
    validate_against_trait_engine,
)
from forest_soul_forge.core.trait_engine import TraitEngine


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIT_TREE_PATH = REPO_ROOT / "config" / "trait_tree.yaml"
GENRES_PATH = REPO_ROOT / "config" / "genres.yaml"
CONSTITUTION_PATH = REPO_ROOT / "config" / "constitution_templates.yaml"


D4_ADVANCED_ROLES = ("test_author", "migration_pilot", "release_gatekeeper")
EXPECTED_DOMAINS = (
    "security", "audit", "cognitive",
    "communication", "emotional", "embodiment",
)


# ---------------------------------------------------------------------------
# trait_tree.yaml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def trait_engine() -> TraitEngine:
    return TraitEngine(TRAIT_TREE_PATH)


@pytest.mark.parametrize("role", D4_ADVANCED_ROLES)
def test_role_in_trait_engine(trait_engine, role):
    assert role in trait_engine.roles, (
        f"role {role!r} missing from trait_tree.yaml — D4 advanced "
        f"rollout (B332) didn't land"
    )


@pytest.mark.parametrize("role", D4_ADVANCED_ROLES)
def test_role_has_six_domain_weights(trait_engine, role):
    weights = trait_engine.roles[role].domain_weights
    assert set(weights.keys()) == set(EXPECTED_DOMAINS), (
        f"{role}: expected exactly {sorted(EXPECTED_DOMAINS)} "
        f"domain weight keys; got {sorted(weights.keys())}"
    )


@pytest.mark.parametrize("role", D4_ADVANCED_ROLES)
def test_role_weights_in_plausible_range(trait_engine, role):
    """Sanity guard against typos — every weight in [0.0, 3.0].
    Existing roles span ~0.4 → 2.6; nothing should be outside that."""
    weights = trait_engine.roles[role].domain_weights
    for domain, w in weights.items():
        assert 0.0 <= w <= 3.0, (
            f"{role}.{domain} = {w} outside [0.0, 3.0]"
        )


def test_release_gatekeeper_audit_weight_is_max(trait_engine):
    """release_gatekeeper.audit = 2.6 is the highest audit weight in
    the system (matches reality_anchor's ceiling per the calibration
    rationale in the draft review)."""
    audits = {
        role: role_def.domain_weights.get("audit", 0.0)
        for role, role_def in trait_engine.roles.items()
    }
    rg_audit = audits["release_gatekeeper"]
    higher = [r for r, a in audits.items() if a > rg_audit]
    assert higher == [], (
        f"release_gatekeeper.audit ({rg_audit}) should be max; "
        f"these roles exceed it: {higher}"
    )


# ---------------------------------------------------------------------------
# genres.yaml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def genre_engine():
    return load_genres(GENRES_PATH)


def test_test_author_in_researcher_genre(genre_engine):
    g = genre_engine.genre_for("test_author")
    assert g is not None and g.name == "researcher"


@pytest.mark.parametrize("role", ("migration_pilot", "release_gatekeeper"))
def test_guardian_roles_in_guardian_genre(genre_engine, role):
    g = genre_engine.genre_for(role)
    assert g is not None and g.name == "guardian", (
        f"{role}: expected guardian; got {g.name if g else 'unclaimed'}"
    )


@pytest.mark.parametrize("role", D4_ADVANCED_ROLES)
def test_role_claimed_exactly_once(genre_engine, role):
    """No role appears under two genres. Critical because the
    risk-profile resolution would silently pick one and the
    operator audit can't tell which without grepping."""
    claiming = [
        g.name for g in genre_engine.all_genres()
        if role in g.roles
    ]
    assert len(claiming) == 1, (
        f"{role} claimed by: {claiming} (expected exactly 1)"
    )


def test_genre_invariant_against_trait_engine(trait_engine, genre_engine):
    """ADR-0021 invariant: every trait-engine role is claimed by
    some genre. Critical post-B332 because we just added three
    new roles; if any aren't claimed the birth path silently
    falls back to genre=None and the kit-tier ceiling check
    bypasses."""
    unclaimed = validate_against_trait_engine(
        genre_engine, list(trait_engine.roles.keys()),
    )
    # If unclaimed is non-empty, fail with the full list so
    # debugging is obvious.
    assert unclaimed == [], (
        f"trait_tree.yaml roles unclaimed by any genre: {unclaimed}"
    )


# ---------------------------------------------------------------------------
# constitution_templates.yaml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def constitution_templates() -> dict:
    """Per-role policy blocks live under `role_base:` in
    constitution_templates.yaml (the file's three-layer composer
    pattern documented at the top of the YAML)."""
    raw = yaml.safe_load(CONSTITUTION_PATH.read_text(encoding="utf-8"))
    return raw.get("role_base", {})


@pytest.mark.parametrize("role", D4_ADVANCED_ROLES)
def test_role_has_template(constitution_templates, role):
    assert role in constitution_templates, (
        f"role {role!r} missing from constitution_templates.yaml"
    )


@pytest.mark.parametrize("role", D4_ADVANCED_ROLES)
def test_template_has_required_blocks(constitution_templates, role):
    """Every constitution template ships policies + risk_thresholds
    + out_of_scope + operator_duties + drift_monitoring."""
    template = constitution_templates[role]
    for required in (
        "policies", "risk_thresholds", "out_of_scope",
        "operator_duties", "drift_monitoring",
    ):
        assert required in template, (
            f"{role}: missing {required} block"
        )


def _policy_ids(template) -> set[str]:
    return {p["id"] for p in template.get("policies", [])
            if isinstance(p, dict) and "id" in p}


def test_test_author_critical_policies(constitution_templates):
    """test_author's two load-bearing forbids must be present:
    no production code edits + no deleting self-authored tests."""
    ids = _policy_ids(constitution_templates["test_author"])
    assert "forbid_production_code_edit" in ids
    assert "forbid_self_test_deletion" in ids
    assert "require_assertion_in_test" in ids


def test_migration_pilot_critical_policies(constitution_templates):
    """migration_pilot's apply-gate discipline + drop-archive
    invariant + rollback-required all present."""
    ids = _policy_ids(constitution_templates["migration_pilot"])
    assert "require_dry_run_before_apply" in ids
    assert "require_human_approval_for_apply" in ids
    assert "forbid_silent_drop" in ids
    assert "require_rollback_plan" in ids


def test_release_gatekeeper_critical_policies(constitution_templates):
    """release_gatekeeper's advisory-only stance: no release acts,
    decisions must cite evidence, can't skip checks."""
    ids = _policy_ids(constitution_templates["release_gatekeeper"])
    assert "forbid_release_action" in ids
    assert "require_conformance_evidence" in ids
    assert "require_fail_explanation" in ids
    assert "forbid_check_skip" in ids


def test_min_confidence_calibration(constitution_templates):
    """Calibration sanity from the draft review:
      test_author      → 0.55 (low; probing speculation)
      migration_pilot  → 0.70 (high; conviction not speculation)
      release_gatekeeper → 0.80 (highest; release decisions)
    """
    thresholds = {
        role: constitution_templates[role]["risk_thresholds"]["min_confidence_to_act"]
        for role in D4_ADVANCED_ROLES
    }
    assert thresholds["test_author"] == 0.55
    assert thresholds["migration_pilot"] == 0.70
    assert thresholds["release_gatekeeper"] == 0.80
