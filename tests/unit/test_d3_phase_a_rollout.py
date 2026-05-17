"""ADR-0078 Phase A (B342-B347) — D3 Local SOC forensic_archivist tests.

Coverage:
  trait_tree.yaml:
    - forensic_archivist parses
    - 6 expected domain_weight keys
    - weights in plausible range [0, 3]
    - audit weight >= 2.0 (chain-of-custody role)

  genres.yaml:
    - forensic_archivist claimed by 'guardian' exactly once
    - no double-claim
    - genre invariant still holds (every trait-engine role claimed)

  constitution_templates.yaml:
    - has policies + risk_thresholds + out_of_scope + operator_duties
      + drift_monitoring blocks
    - has forbid_artifact_mutation + require_chain_of_custody_log +
      forbid_silent_archive + require_integrity_hash_verification
    - min_confidence_to_act = 0.75 (between researcher's 0.55 and
      release_gatekeeper's 0.80 — verification-class)

  tool_catalog.yaml:
    - archetype entry exists
    - kit is genuinely read_only: NO shell_exec, NO code_edit, NO
      external-side-effect tools. file_integrity + audit_chain_verify
      + code_read for inspection; memory_write only for the custody
      log in the agent's private memory scope
    - kit invariant: every tool in the kit has side_effects ∈
      {read_only, network} where network is limited to memory ops

  config/domains/d3_local_soc.yaml:
    - forensic_archive capability listed
    - entry_agent (role=forensic_archivist, capability=forensic_archive)
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
TOOL_CATALOG_PATH = REPO_ROOT / "config" / "tool_catalog.yaml"
D3_MANIFEST_PATH = REPO_ROOT / "config" / "domains" / "d3_local_soc.yaml"


D3_PHASE_A_ROLE = "forensic_archivist"
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


def test_role_in_trait_engine(trait_engine):
    assert D3_PHASE_A_ROLE in trait_engine.roles, (
        f"role {D3_PHASE_A_ROLE!r} missing from trait_tree.yaml — "
        f"D3 Phase A (B343) didn't land"
    )


def test_role_has_six_domain_weights(trait_engine):
    weights = trait_engine.roles[D3_PHASE_A_ROLE].domain_weights
    assert set(weights.keys()) == set(EXPECTED_DOMAINS), (
        f"{D3_PHASE_A_ROLE}: expected exactly {sorted(EXPECTED_DOMAINS)} "
        f"domain weight keys; got {sorted(weights.keys())}"
    )


def test_role_weights_in_plausible_range(trait_engine):
    weights = trait_engine.roles[D3_PHASE_A_ROLE].domain_weights
    for domain, w in weights.items():
        assert 0.0 <= w <= 3.0, (
            f"{D3_PHASE_A_ROLE}.{domain} = {w} outside [0.0, 3.0]"
        )


def test_role_audit_weight_meaningful(trait_engine):
    """Chain-of-custody is fundamentally an audit role. The audit
    weight must be >= 2.0 to land in the audit-discipline cluster
    (where verifier_loop / reality_anchor / release_gatekeeper sit)."""
    weights = trait_engine.roles[D3_PHASE_A_ROLE].domain_weights
    assert weights["audit"] >= 2.0, (
        f"{D3_PHASE_A_ROLE}.audit = {weights['audit']} should be "
        f">= 2.0 — chain-of-custody is an audit-discipline role"
    )


# ---------------------------------------------------------------------------
# genres.yaml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def genre_engine():
    return load_genres(GENRES_PATH)


def test_role_in_guardian_genre(genre_engine):
    """forensic_archivist's kit is genuinely read_only (no
    shell_exec, no code_edit, no external) so guardian is
    correct. Contrast with B341 — migration_pilot + release_
    gatekeeper had to move to actuator because their kits
    included shell_exec. Genre = action surface, not stance."""
    g = genre_engine.genre_for(D3_PHASE_A_ROLE)
    assert g is not None and g.name == "guardian", (
        f"{D3_PHASE_A_ROLE}: expected guardian; got "
        f"{g.name if g else 'unclaimed'}"
    )


def test_role_claimed_exactly_once(genre_engine):
    claiming = [
        g.name for g in genre_engine.all_genres()
        if D3_PHASE_A_ROLE in g.roles
    ]
    assert len(claiming) == 1, (
        f"{D3_PHASE_A_ROLE} claimed by: {claiming} (expected exactly 1)"
    )


def test_genre_invariant_against_trait_engine(trait_engine, genre_engine):
    """ADR-0021 invariant: every trait-engine role is claimed by
    some genre. Re-checking after B343 added forensic_archivist."""
    unclaimed = validate_against_trait_engine(
        genre_engine, list(trait_engine.roles.keys()),
    )
    assert unclaimed == [], (
        f"trait_tree.yaml roles unclaimed by any genre: {unclaimed}"
    )


# ---------------------------------------------------------------------------
# constitution_templates.yaml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def constitution_templates() -> dict:
    raw = yaml.safe_load(CONSTITUTION_PATH.read_text(encoding="utf-8"))
    return raw.get("role_base", {})


def test_role_has_template(constitution_templates):
    assert D3_PHASE_A_ROLE in constitution_templates, (
        f"role {D3_PHASE_A_ROLE!r} missing from constitution_templates.yaml"
    )


def test_template_has_required_blocks(constitution_templates):
    template = constitution_templates[D3_PHASE_A_ROLE]
    for required in (
        "policies", "risk_thresholds", "out_of_scope",
        "operator_duties", "drift_monitoring",
    ):
        assert required in template, (
            f"{D3_PHASE_A_ROLE}: missing {required} block"
        )


def _policy_ids(template) -> set[str]:
    return {p["id"] for p in template.get("policies", [])
            if isinstance(p, dict) and "id" in p}


def test_forensic_archivist_critical_policies(constitution_templates):
    """The four load-bearing policies that define chain-of-custody:
      - forbid_artifact_mutation: the archivist never modifies bytes
      - require_chain_of_custody_log: every transition is attested
      - forbid_silent_archive: no untracked moves
      - require_integrity_hash_verification: re-verify on every touch
    """
    ids = _policy_ids(constitution_templates[D3_PHASE_A_ROLE])
    assert "forbid_artifact_mutation" in ids
    assert "require_chain_of_custody_log" in ids
    assert "forbid_silent_archive" in ids
    assert "require_integrity_hash_verification" in ids


def test_min_confidence_calibration(constitution_templates):
    """0.75 — verification-class confidence. Below release_gatekeeper
    (0.80, release decisions are final) and above migration_pilot
    (0.70, dry-runs forgive ambiguity). Chain-of-custody attestations
    are read-only but become operator-trusted evidence; the threshold
    has to discourage hedging."""
    threshold = (
        constitution_templates[D3_PHASE_A_ROLE]
        ["risk_thresholds"]["min_confidence_to_act"]
    )
    assert threshold == 0.75


# ---------------------------------------------------------------------------
# tool_catalog.yaml archetype kit
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tool_catalog():
    from forest_soul_forge.core.tool_catalog import load_catalog
    return load_catalog(TOOL_CATALOG_PATH)


def test_role_has_archetype_kit(tool_catalog):
    """Without an archetype entry the role would inherit guardian's
    default_kit_pattern (content_review + policy_check + audit_inspection)
    — none of which include file_integrity. Explicit override is required."""
    assert D3_PHASE_A_ROLE in tool_catalog.archetypes, (
        f"role {D3_PHASE_A_ROLE!r} missing from tool_catalog.yaml "
        f"archetypes — birth will fall back to guardian default kit"
    )


def _kit_names(tool_catalog, role):
    return {ref.name for ref in tool_catalog.archetypes[role].standard_tools}


def test_forensic_archivist_kit_has_core_verification_tools(tool_catalog):
    """The minimum-viable chain-of-custody kit: file_integrity to
    sha256 artifacts, audit_chain_verify to confirm the chain itself
    is intact before attesting, code_read for metadata files."""
    kit = _kit_names(tool_catalog, D3_PHASE_A_ROLE)
    assert "file_integrity" in kit
    assert "audit_chain_verify" in kit
    assert "code_read" in kit
    assert "memory_write" in kit  # custody log
    assert "memory_recall" in kit  # prior chain lookup


def test_forensic_archivist_kit_is_read_only(tool_catalog):
    """Guardian-ceiling invariant: no shell_exec, no code_edit, no
    external-side-effect tools. The apply path is operator-driven —
    the archivist verifies and logs; the operator moves bytes."""
    kit = _kit_names(tool_catalog, D3_PHASE_A_ROLE)
    forbidden = {
        "shell_exec", "code_edit", "browser_action",
        "mcp_call", "external_messaging",
    }
    intersection = kit & forbidden
    assert intersection == set(), (
        f"forensic_archivist kit must be read_only; found "
        f"action tools: {sorted(intersection)}"
    )


def test_forensic_archivist_kit_within_guardian_ceiling(tool_catalog):
    """Stricter check: every tool in the kit must have side_effects
    in {read_only, network}. This is the kit-tier ceiling check that
    the genre engine enforces at birth — running it here catches
    drift before a birth attempt fails.

    Guardian genre's max_side_effects is `read_only`, but per the
    ADR-0021-amendment + genres.yaml comment, network is permitted
    for memory ops which is what memory_recall/memory_write actually
    use (they hit local DB, but their declared side_effect class is
    network in some entries — accept both to keep the check honest
    against the actual schema)."""
    GUARDIAN_OK = {"read_only", "network"}
    kit = tool_catalog.archetypes[D3_PHASE_A_ROLE].standard_tools
    violations = []
    for ref in kit:
        try:
            tool = tool_catalog.get_tool(ref)
        except Exception:
            continue  # unknown tool — different test catches that
        if tool.side_effects not in GUARDIAN_OK:
            violations.append((ref.name, tool.side_effects))
    assert violations == [], (
        f"forensic_archivist kit violates guardian ceiling "
        f"(allowed: {GUARDIAN_OK}); found: {violations}"
    )


# ---------------------------------------------------------------------------
# config/domains/d3_local_soc.yaml
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def d3_manifest():
    return yaml.safe_load(D3_MANIFEST_PATH.read_text(encoding="utf-8"))


def test_d3_manifest_lists_forensic_archive_capability(d3_manifest):
    """The capability must be in the manifest's capabilities list so
    the orchestrator's decompose_intent step can route to it."""
    assert "forensic_archive" in d3_manifest.get("capabilities", []), (
        "d3 manifest missing forensic_archive capability — "
        "orchestrator routing will fall through"
    )


def test_d3_manifest_entry_agent_for_forensic_archive(d3_manifest):
    """entry_agents wires (role → capability) so route_to_domain
    knows which role handles forensic_archive."""
    entries = d3_manifest.get("entry_agents", [])
    matches = [
        e for e in entries
        if e.get("role") == D3_PHASE_A_ROLE
        and e.get("capability") == "forensic_archive"
    ]
    assert len(matches) == 1, (
        f"d3 manifest must have exactly one entry_agent "
        f"(role={D3_PHASE_A_ROLE}, capability=forensic_archive); "
        f"found {len(matches)}"
    )
