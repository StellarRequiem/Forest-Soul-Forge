"""ADR-0078 Phase A T3 (B345) — D3 Local SOC handoffs.yaml wiring.

Coverage:
  handoffs.yaml structural integrity:
    - file loads cleanly with no errors
    - (d3_local_soc, forensic_archive) maps to archive_evidence.v1
    - pre-existing D3 mappings (incident_summary, incident_response)
      still present (regression guard)
    - the d3.incident_response → d8.compliance_scan cascade rule
      from ADR-0067 T4 is still present (regression guard)

  resolve_route happy path with ForensicArchivist-D3 in the inventory:
    - subintent for d3_local_soc.forensic_archive resolves to a
      ResolvedRoute pointing at ForensicArchivist-D3 with skill_ref =
      archive_evidence.v1

  resolve_route fail paths (D3 Phase A pre-birth state):
    - subintent for forensic_archive with empty agent_inventory →
      UNROUTABLE_NO_ALIVE_AGENT (operator-visible signal that the
      birth-forensic-archivist.command hasn't run yet)

  cascade behavior:
    - no NEW outbound cascade from forensic_archive (deliberate per
      ADR-0078; auto-archive cascade deferred to Phase D + ADR-0066)
    - pre-existing d3.incident_response → d8.compliance_scan still
      fires when d8 is live (regression guard)

  domain manifest:
    - d3_local_soc's entry_agents includes (forensic_archivist,
      forensic_archive) AND retains the original seven entries
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
    UNROUTABLE_NO_ALIVE_AGENT,
    ResolvedRoute,
    UnroutableSubIntent,
    apply_cascade_rules,
    load_handoffs,
    resolve_route,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFFS_PATH = REPO_ROOT / "config" / "handoffs.yaml"
D3_MANIFEST_PATH = REPO_ROOT / "config" / "domains" / "d3_local_soc.yaml"


# ---------------------------------------------------------------------------
# Structural integrity
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def handoffs_config():
    cfg, errors = load_handoffs(HANDOFFS_PATH)
    assert errors == [], f"handoffs.yaml load errors: {errors}"
    return cfg


def test_forensic_archive_mapping_present(handoffs_config):
    """B345 added (d3_local_soc, forensic_archive) → archive_evidence.v1.
    The skill itself doesn't ship until B346; the mapping declares
    intent so route_to_domain.v1 can resolve immediately."""
    key = ("d3_local_soc", "forensic_archive")
    assert key in handoffs_config.default_skill_per_capability, (
        f"missing mapping for {key} — ADR-0078 Phase A T3 didn't land"
    )
    skill = handoffs_config.default_skill_per_capability[key]
    assert skill.skill_name == "archive_evidence"
    assert skill.skill_version == "1"


def test_pre_existing_d3_mappings_still_present(handoffs_config):
    """Regression guard: D3's pre-B345 mappings must survive the
    edit. Append-only discipline on handoffs.yaml."""
    for key in (
        ("d3_local_soc", "incident_summary"),
        ("d3_local_soc", "incident_response"),
    ):
        assert key in handoffs_config.default_skill_per_capability, (
            f"pre-existing mapping {key} was removed during B345 edit"
        )


def test_d3_to_d8_cascade_still_present(handoffs_config):
    """The cascade rule from ADR-0067 T4 must still be live.
    ADR-0078 explicitly noted this as the existing wiring path
    (Decision 4); if it vanished during B345's edit, the SOC →
    compliance evidence chain breaks."""
    matched = [
        r for r in handoffs_config.cascade_rules
        if r.source_domain == "d3_local_soc"
        and r.source_capability == "incident_response"
        and r.target_domain == "d8_compliance"
        and r.target_capability == "compliance_scan"
    ]
    assert len(matched) == 1, (
        f"d3.incident_response → d8.compliance_scan cascade not "
        f"found; matched: {matched}"
    )


# ---------------------------------------------------------------------------
# D3 manifest entry_agents
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def d3_manifest():
    raw = yaml.safe_load(D3_MANIFEST_PATH.read_text(encoding="utf-8"))
    return raw


def test_d3_entry_agents_include_forensic_archivist(d3_manifest):
    """B343 added (forensic_archivist, forensic_archive) to
    entry_agents. Regression guard ensures B345's edits didn't
    drop it."""
    pairs = [
        (e["role"], e["capability"])
        for e in d3_manifest["entry_agents"]
    ]
    assert ("forensic_archivist", "forensic_archive") in pairs, (
        "d3_local_soc.yaml entry_agents missing "
        "(forensic_archivist, forensic_archive)"
    )


def test_d3_original_seven_still_in_entry_agents(d3_manifest):
    """The original Security Swarm seven (ADR-0033) must survive
    every D3 advanced-rollout edit. Phase A through D adds; never
    removes."""
    pairs = [
        (e["role"], e["capability"])
        for e in d3_manifest["entry_agents"]
    ]
    for pair in [
        ("response_rogue",  "incident_response"),
        ("log_lurker",      "log_monitoring"),
        ("anomaly_ace",     "anomaly_detection"),
        ("vault_warden",    "forensic_cleanup"),
        ("patch_patrol",    "vulnerability_check"),
        ("gatekeeper",      "access_audit"),
        ("deception_duke",  "deception_management"),
    ]:
        assert pair in pairs, (
            f"d3_local_soc.yaml entry_agents lost original "
            f"Security Swarm entry {pair}"
        )


# ---------------------------------------------------------------------------
# resolve_route — D3 Phase A paths
# ---------------------------------------------------------------------------


def _d3_registry() -> DomainRegistry:
    """Build a registry mirroring the D3 manifest's actual shape:
    all original Security Swarm entries + the new
    forensic_archivist entry. D8 stays at status='live' so the
    pre-existing cascade test stays useful."""
    return DomainRegistry(domains=(
        Domain(
            domain_id="d3_local_soc",
            name="Local Security Operations Center",
            status="partial",   # matches manifest
            description="",
            entry_agents=(
                EntryAgent(role="response_rogue",  capability="incident_response"),
                EntryAgent(role="log_lurker",      capability="log_monitoring"),
                EntryAgent(role="anomaly_ace",     capability="anomaly_detection"),
                EntryAgent(role="vault_warden",    capability="forensic_cleanup"),
                EntryAgent(role="patch_patrol",    capability="vulnerability_check"),
                EntryAgent(role="gatekeeper",      capability="access_audit"),
                EntryAgent(role="deception_duke",  capability="deception_management"),
                EntryAgent(role="forensic_archivist", capability="forensic_archive"),
            ),
            capabilities=(
                "incident_response", "log_monitoring", "anomaly_detection",
                "forensic_cleanup", "forensic_archive", "vulnerability_check",
                "access_audit", "deception_management",
                "threat_hunting", "incident_summary", "canary_status",
            ),
            example_intents=(),
        ),
        Domain(
            domain_id="d8_compliance",
            name="Compliance",
            status="live",
            description="",
            entry_agents=(
                EntryAgent(role="compliance_auditor", capability="compliance_scan"),
            ),
            capabilities=("compliance_scan",),
            example_intents=(),
        ),
    ))


def _d3_alive_inventory(*, include_archivist: bool) -> list[dict]:
    """Inventory with the original Security Swarm always alive;
    forensic_archivist is optional so we can test both pre-birth
    and post-birth states."""
    base = [
        {"instance_id": "rr_1", "role": "response_rogue",  "status": "active"},
        {"instance_id": "ll_1", "role": "log_lurker",      "status": "active"},
        {"instance_id": "aa_1", "role": "anomaly_ace",     "status": "active"},
        {"instance_id": "vw_1", "role": "vault_warden",    "status": "active"},
        {"instance_id": "pp_1", "role": "patch_patrol",    "status": "active"},
        {"instance_id": "gk_1", "role": "gatekeeper",      "status": "active"},
        {"instance_id": "dd_1", "role": "deception_duke",  "status": "active"},
        {"instance_id": "comp_1", "role": "compliance_auditor", "status": "active"},
    ]
    if include_archivist:
        base.append(
            {"instance_id": "fa_1", "role": "forensic_archivist", "status": "active"}
        )
    return base


def test_resolve_route_happy_path_with_archivist_alive(handoffs_config):
    """Post-birth state: resolve a forensic_archive subintent and
    confirm it points at the forensic_archivist instance + the
    archive_evidence.v1 skill."""
    registry = _d3_registry()
    inv = _d3_alive_inventory(include_archivist=True)
    subintent = {
        "intent": "preserve evidence from incident #INC-2026-001",
        "domain": "d3_local_soc",
        "capability": "forensic_archive",
        "confidence": 0.92,
        "status": "routable",
    }
    result = resolve_route(subintent, registry, handoffs_config, inv)
    assert isinstance(result, ResolvedRoute), (
        f"expected ResolvedRoute; got {result!r}"
    )
    assert result.target_capability == "forensic_archive"
    assert result.skill_ref.skill_name == "archive_evidence"
    assert result.skill_ref.skill_version == "1"
    assert result.target_instance_id == "fa_1"


def test_resolve_route_pre_birth_returns_no_alive_agent(handoffs_config):
    """Pre-birth state: handoffs + domain manifest declare the
    capability; agent inventory doesn't include the archivist yet.
    Operator-visible signal that birth-forensic-archivist.command
    hasn't run."""
    registry = _d3_registry()
    inv = _d3_alive_inventory(include_archivist=False)
    subintent = {
        "intent": "pre-birth forensic_archive test",
        "domain": "d3_local_soc",
        "capability": "forensic_archive",
        "confidence": 0.9,
        "status": "routable",
    }
    result = resolve_route(subintent, registry, handoffs_config, inv)
    assert isinstance(result, UnroutableSubIntent)
    assert result.code == UNROUTABLE_NO_ALIVE_AGENT


# ---------------------------------------------------------------------------
# Cascade behavior
# ---------------------------------------------------------------------------


def test_d3_incident_response_cascade_fires_when_d8_live(handoffs_config):
    """ADR-0067 T4 cascade still works post-B345 edit. The
    incident_response route should produce a follow-on
    compliance_scan in d8."""
    registry = _d3_registry()
    inv = _d3_alive_inventory(include_archivist=True)
    initial = ResolvedRoute(
        target_domain="d3_local_soc",
        target_capability="incident_response",
        target_instance_id="rr_1",
        skill_ref=handoffs_config.default_skill_per_capability[
            ("d3_local_soc", "incident_response")
        ],
        intent="respond to INC-2026-001",
        confidence=0.9,
        reason="initial route",
    )
    cascades = apply_cascade_rules(initial, handoffs_config, registry, inv)
    matching = [
        c for c in cascades
        if isinstance(c, ResolvedRoute)
        and c.target_domain == "d8_compliance"
        and c.target_capability == "compliance_scan"
    ]
    assert len(matching) == 1
    assert matching[0].is_cascade is True
    assert matching[0].cascade_source_domain == "d3_local_soc"
    assert matching[0].cascade_source_capability == "incident_response"


def test_forensic_archive_has_no_outbound_cascade(handoffs_config):
    """ADR-0078 Phase A deliberately does NOT cascade from
    forensic_archive. A d3.incident_response → d3.forensic_archive
    cascade would be attractive ('every incident auto-preserves
    evidence') but would inflate the audit chain with attestations
    operators may never consult. Phase D (SOAR playbooks, ADR-0066)
    is the right home for that — a playbook step can decide WHICH
    incidents need auto-archive based on severity. Until then,
    forensic_archive is operator-triggered."""
    matched = [
        r for r in handoffs_config.cascade_rules
        if r.source_domain == "d3_local_soc"
        and r.source_capability == "forensic_archive"
    ]
    assert matched == [], (
        f"unexpected cascade rule from d3_local_soc.forensic_archive; "
        f"Phase A is terminal per ADR-0078: {matched}"
    )
