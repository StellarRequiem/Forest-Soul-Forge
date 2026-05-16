"""ADR-0077 T3 (B334) — D4 advanced rollout handoffs.yaml wiring.

Coverage:
  handoffs.yaml structural integrity:
    - file loads cleanly with no errors
    - the three new (domain, capability) mappings are present
      with the correct skill_name + skill_version
    - the d4.review_signoff → d8.compliance_scan cascade rule is
      still present (regression guard from ADR-0067 T4)

  resolve_route happy path with TestAuthor-D4 in the inventory:
    - subintent for d4_code_review.test_proposal resolves to a
      ResolvedRoute pointing at TestAuthor-D4 with skill_ref =
      propose_tests.v1
    - same for migration_safety + release_gating

  resolve_route fail paths (D4 pre-birth state):
    - subintent for test_proposal with empty agent_inventory →
      UNROUTABLE_NO_ALIVE_AGENT (operator-visible signal that
      birth hasn't run yet)
    - subintent for test_proposal with the WRONG role alive
      (only software_engineer) → still UNROUTABLE_NO_ALIVE_AGENT

  cascade behavior (regression):
    - d4.review_signoff → d8.compliance_scan still fires when
      d8 is alive
    - cascade returns UNROUTABLE_DOMAIN_PLANNED when d8 is
      status='planned' (the operator-visible signal documented
      in ADR-0077 §Negative consequences)

  domain manifest:
    - d4_code_review's entry_agents list now includes the three
      new (role, capability) pairs from ADR-0077 T3
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.domain_registry import (
    Domain,
    DomainRegistry,
    EntryAgent,
)
from forest_soul_forge.core.routing_engine import (
    UNROUTABLE_DOMAIN_PLANNED,
    UNROUTABLE_NO_ALIVE_AGENT,
    ResolvedRoute,
    UnroutableSubIntent,
    apply_cascade_rules,
    load_handoffs,
    resolve_route,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFFS_PATH = REPO_ROOT / "config" / "handoffs.yaml"
D4_MANIFEST_PATH = REPO_ROOT / "config" / "domains" / "d4_code_review.yaml"


# ---------------------------------------------------------------------------
# Structural integrity
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def handoffs_config():
    cfg, errors = load_handoffs(HANDOFFS_PATH)
    # Soft errors are OK in principle; fail loud if any present
    # so a malformed entry can't slip in.
    assert errors == [], f"handoffs.yaml load errors: {errors}"
    return cfg


@pytest.mark.parametrize("capability,skill_name", [
    ("test_proposal",    "propose_tests"),
    ("migration_safety", "safe_migration"),
    ("release_gating",   "release_check"),
])
def test_d4_new_skill_mapping_present(handoffs_config, capability, skill_name):
    key = ("d4_code_review", capability)
    assert key in handoffs_config.default_skill_per_capability, (
        f"missing mapping for {key} — ADR-0077 T3 didn't land"
    )
    skill = handoffs_config.default_skill_per_capability[key]
    assert skill.skill_name == skill_name
    assert skill.skill_version == "1"


def test_pre_existing_d4_mappings_still_present(handoffs_config):
    """Regression guard: ADR-0067 T4 mappings must survive the
    T3 edit. Append-only discipline on handoffs.yaml."""
    for key in (
        ("d4_code_review", "review_signoff"),
        ("d4_code_review", "implementation"),
    ):
        assert key in handoffs_config.default_skill_per_capability, (
            f"pre-existing mapping {key} was removed during T3 edit"
        )


def test_d4_to_d8_cascade_still_present(handoffs_config):
    """The cascade rule from ADR-0067 T4 must still be live.
    ADR-0077 declared it as the existing wiring path; if it
    vanished during the T3 edit, the cascade chain breaks."""
    matched = [
        r for r in handoffs_config.cascade_rules
        if r.source_domain == "d4_code_review"
        and r.source_capability == "review_signoff"
        and r.target_domain == "d8_compliance"
        and r.target_capability == "compliance_scan"
    ]
    assert len(matched) == 1, (
        f"d4.review_signoff → d8.compliance_scan cascade not "
        f"found; matched: {matched}"
    )


# ---------------------------------------------------------------------------
# D4 manifest entry_agents
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def d4_manifest():
    raw = yaml.safe_load(D4_MANIFEST_PATH.read_text(encoding="utf-8"))
    return raw


@pytest.mark.parametrize("role,capability", [
    ("test_author",        "test_proposal"),
    ("migration_pilot",    "migration_safety"),
    ("release_gatekeeper", "release_gating"),
])
def test_d4_entry_agents_include_advanced_roles(d4_manifest, role, capability):
    pairs = [
        (e["role"], e["capability"])
        for e in d4_manifest["entry_agents"]
    ]
    assert (role, capability) in pairs, (
        f"d4_code_review.yaml entry_agents missing ({role}, {capability})"
    )


def test_d4_original_triune_still_in_entry_agents(d4_manifest):
    """The original triune (system_architect / software_engineer /
    code_reviewer) must survive the T3 edit."""
    pairs = [
        (e["role"], e["capability"])
        for e in d4_manifest["entry_agents"]
    ]
    for pair in [
        ("system_architect",  "architectural_design"),
        ("software_engineer", "implementation"),
        ("code_reviewer",     "review_signoff"),
    ]:
        assert pair in pairs, (
            f"d4_code_review.yaml entry_agents lost original "
            f"triune entry {pair}"
        )


# ---------------------------------------------------------------------------
# resolve_route — D4 advanced rollout paths
# ---------------------------------------------------------------------------


def _d4_registry(d8_status: str = "live") -> DomainRegistry:
    """Build a registry where D4 lists all six entry agents
    (original 3 + advanced 3). D8's status is configurable so we
    can test both live (cascade succeeds) and planned (cascade
    returns UNROUTABLE_DOMAIN_PLANNED)."""
    return DomainRegistry(domains=(
        Domain(
            domain_id="d4_code_review",
            name="Code Review",
            status="live",
            description="",
            entry_agents=(
                EntryAgent(role="system_architect",  capability="architectural_design"),
                EntryAgent(role="software_engineer", capability="implementation"),
                EntryAgent(role="code_reviewer",     capability="review_signoff"),
                EntryAgent(role="test_author",       capability="test_proposal"),
                EntryAgent(role="migration_pilot",   capability="migration_safety"),
                EntryAgent(role="release_gatekeeper", capability="release_gating"),
            ),
            capabilities=(
                "architectural_design", "implementation",
                "review_signoff", "test_proposal",
                "migration_safety", "release_gating",
            ),
            example_intents=(),
        ),
        Domain(
            domain_id="d8_compliance",
            name="Compliance",
            status=d8_status,
            description="",
            entry_agents=(
                EntryAgent(role="compliance_auditor", capability="compliance_scan"),
            ) if d8_status == "live" else (),
            capabilities=("compliance_scan",),
            example_intents=(),
        ),
    ))


def _d4_alive_inventory(*, include_advanced: bool) -> list[dict]:
    """Inventory with the original triune always alive; the three
    advanced agents are optional so we can test both pre-birth
    and post-birth states."""
    base = [
        {"instance_id": "arch_1", "role": "system_architect",  "status": "active"},
        {"instance_id": "eng_1",  "role": "software_engineer", "status": "active"},
        {"instance_id": "rev_1",  "role": "code_reviewer",     "status": "active"},
        {"instance_id": "comp_1", "role": "compliance_auditor", "status": "active"},
    ]
    if include_advanced:
        base.extend([
            {"instance_id": "ta_1",  "role": "test_author",       "status": "active"},
            {"instance_id": "mp_1",  "role": "migration_pilot",   "status": "active"},
            {"instance_id": "rg_1",  "role": "release_gatekeeper", "status": "active"},
        ])
    return base


@pytest.mark.parametrize("capability,expected_skill,expected_role", [
    ("test_proposal",    "propose_tests",   "test_author"),
    ("migration_safety", "safe_migration",  "migration_pilot"),
    ("release_gating",   "release_check",   "release_gatekeeper"),
])
def test_resolve_route_happy_path_with_advanced_agents(
    handoffs_config, capability, expected_skill, expected_role,
):
    """After the three birth scripts have run, resolving a sub-
    intent for the new capabilities should return a ResolvedRoute
    pointing at the right agent + skill."""
    registry = _d4_registry()
    inv = _d4_alive_inventory(include_advanced=True)
    subintent = {
        "intent": f"test: {capability}",
        "domain": "d4_code_review",
        "capability": capability,
        "confidence": 0.9,
        "status": "routable",
    }
    result = resolve_route(subintent, registry, handoffs_config, inv)
    assert isinstance(result, ResolvedRoute), (
        f"expected ResolvedRoute; got {result!r}"
    )
    assert result.target_capability == capability
    assert result.skill_ref.skill_name == expected_skill
    assert result.skill_ref.skill_version == "1"
    # The resolved instance should be the one whose role matches
    # the capability's declared entry_agent.
    expected_instances = {
        "test_author": "ta_1",
        "migration_pilot": "mp_1",
        "release_gatekeeper": "rg_1",
    }
    assert result.target_instance_id == expected_instances[expected_role]


@pytest.mark.parametrize("capability", [
    "test_proposal", "migration_safety", "release_gating",
])
def test_resolve_route_pre_birth_returns_no_alive_agent(
    handoffs_config, capability,
):
    """Pre-birth state: handoffs + domain manifest declare the
    capability; agent inventory doesn't include the new role.
    Operator-visible signal that birth hasn't run yet."""
    registry = _d4_registry()
    inv = _d4_alive_inventory(include_advanced=False)
    subintent = {
        "intent": "pre-birth test",
        "domain": "d4_code_review",
        "capability": capability,
        "confidence": 0.9,
        "status": "routable",
    }
    result = resolve_route(subintent, registry, handoffs_config, inv)
    assert isinstance(result, UnroutableSubIntent)
    assert result.code == UNROUTABLE_NO_ALIVE_AGENT


# ---------------------------------------------------------------------------
# Cascade behavior
# ---------------------------------------------------------------------------


def test_d4_review_signoff_cascade_fires_when_d8_live(handoffs_config):
    """ADR-0067 T4 cascade still works post-T3 edit. The
    review_signoff route should produce a follow-on
    compliance_scan in d8 when d8 is dispatchable."""
    registry = _d4_registry(d8_status="live")
    inv = _d4_alive_inventory(include_advanced=True)
    initial = ResolvedRoute(
        target_domain="d4_code_review",
        target_capability="review_signoff",
        target_instance_id="rev_1",
        skill_ref=handoffs_config.default_skill_per_capability[
            ("d4_code_review", "review_signoff")
        ],
        intent="review PR #42",
        confidence=0.9,
        reason="initial route",
    )
    cascades = apply_cascade_rules(initial, handoffs_config, registry, inv)
    assert len(cascades) >= 1
    matching = [
        c for c in cascades
        if isinstance(c, ResolvedRoute)
        and c.target_domain == "d8_compliance"
        and c.target_capability == "compliance_scan"
    ]
    assert len(matching) == 1
    assert matching[0].is_cascade is True
    assert matching[0].cascade_source_domain == "d4_code_review"
    assert matching[0].cascade_source_capability == "review_signoff"


def test_d4_review_signoff_cascade_refused_when_d8_planned(handoffs_config):
    """When D8 is status='planned', the cascade should return an
    UnroutableSubIntent with code=domain_planned. ADR-0077 calls
    this out as a designed failure mode — operators see
    `cascade_refused: domain_planned` rather than silent failure."""
    registry = _d4_registry(d8_status="planned")
    inv = _d4_alive_inventory(include_advanced=True)
    initial = ResolvedRoute(
        target_domain="d4_code_review",
        target_capability="review_signoff",
        target_instance_id="rev_1",
        skill_ref=handoffs_config.default_skill_per_capability[
            ("d4_code_review", "review_signoff")
        ],
        intent="review PR #42",
        confidence=0.9,
        reason="initial route",
    )
    cascades = apply_cascade_rules(initial, handoffs_config, registry, inv)
    # Cascade attempt happened; result is unroutable, NOT silently
    # dropped from the cascades list.
    assert len(cascades) >= 1
    refused = [
        c for c in cascades
        if isinstance(c, UnroutableSubIntent) and c.domain == "d8_compliance"
    ]
    assert len(refused) == 1
    assert refused[0].code == UNROUTABLE_DOMAIN_PLANNED


def test_new_d4_capabilities_have_no_outbound_cascades(handoffs_config):
    """test_proposal / migration_safety / release_gating are
    terminal by design (ADR-0077 §Cascade rule wiring: defers the
    d4.release_gating → d1.index_artifact cascade until D1 rolls
    out). Verify no cascade rules reference them as source."""
    for cap in ("test_proposal", "migration_safety", "release_gating"):
        matched = [
            r for r in handoffs_config.cascade_rules
            if r.source_domain == "d4_code_review"
            and r.source_capability == cap
        ]
        assert matched == [], (
            f"unexpected cascade rule from d4_code_review.{cap}; "
            f"ADR-0077 declared the new capabilities terminal: {matched}"
        )
