#!/usr/bin/env python3
"""End-to-end smoke test + demo.

Loads the trait tree, builds one profile per role, generates a soul.md for each,
writes them to examples/, and also generates:
  - two "stress test" profiles with intentional overrides to show flagged output
  - one parent + child + grandchild chain demonstrating lineage inheritance
  - a constitution YAML for every soul, bound by hash into the soul frontmatter
  - an audit chain (examples/audit_chain.jsonl) with agent_created events for
    every soul we write; verified at the end

Run from repo root:
    python3 scripts/demo_generate_soul.py

Produces files in examples/.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running before the package is installed: prepend src/ to sys.path.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from forest_soul_forge.core.audit_chain import AuditChain  # noqa: E402
from forest_soul_forge.core.constitution import build as build_constitution  # noqa: E402
from forest_soul_forge.core.dna import Lineage, dna_short, verify  # noqa: E402
from forest_soul_forge.core.grading import grade  # noqa: E402
from forest_soul_forge.core.trait_engine import TraitEngine  # noqa: E402
from forest_soul_forge.soul.generator import SoulGenerator  # noqa: E402


def _write_constitution_and_soul(
    *,
    gen: SoulGenerator,
    engine: TraitEngine,
    profile,
    agent_name: str,
    out_dir: Path,
    stem: str,
    templates_path: Path,
    lineage: Lineage | None = None,
    audit: AuditChain | None = None,
    audit_event_type: str = "agent_created",
):
    """Build constitution + soul, write both, record an audit event.

    Returns (soul_doc, constitution_obj).
    """
    constitution = build_constitution(
        profile,
        engine,
        agent_name=agent_name,
        templates_path=templates_path,
    )
    constitution_filename = f"{stem}.constitution.yaml"
    constitution_path = out_dir / constitution_filename
    constitution_path.write_text(constitution.to_yaml(), encoding="utf-8")

    soul_doc = gen.generate(
        profile,
        agent_name=agent_name,
        lineage=lineage,
        constitution_hash=constitution.constitution_hash,
        constitution_file=constitution_filename,
    )
    soul_path = out_dir / f"{stem}.soul.md"
    soul_doc.write(soul_path)

    if audit is not None:
        audit.append(
            audit_event_type,
            {
                "agent_name": agent_name,
                "role": profile.role,
                "soul_file": soul_path.name,
                "constitution_file": constitution_filename,
                "constitution_hash": constitution.constitution_hash,
            },
            agent_dna=soul_doc.dna,
        )

    return soul_doc, constitution, soul_path, constitution_path


def main() -> int:
    yaml_path = REPO_ROOT / "config" / "trait_tree.yaml"
    templates_path = REPO_ROOT / "config" / "constitution_templates.yaml"
    out_dir = REPO_ROOT / "examples"
    out_dir.mkdir(exist_ok=True)

    # Reset the demo audit chain so each run is reproducible. The real
    # operator chain under audit/ is untouched by this script.
    # Prefer truncate-in-place over unlink — some mounts (containerized dev
    # envs) allow writes but forbid unlink on bind-mounted files.
    demo_chain_path = out_dir / "audit_chain.jsonl"
    if demo_chain_path.exists():
        with demo_chain_path.open("w", encoding="utf-8"):
            pass  # truncate to zero bytes; AuditChain genesis will fill it

    engine = TraitEngine(yaml_path)
    gen = SoulGenerator(engine)
    audit = AuditChain(demo_chain_path)  # writes genesis synchronously

    print(f"Trait tree v{engine.version}")
    print(f"  domains:   {len(engine.domains)}")
    print(f"  traits:    {len(engine.list_traits())}")
    print(f"  roles:     {len(engine.roles)}")
    print(f"  flagged:   {len(engine.flagged_combinations)}")
    print(f"  audit:     {demo_chain_path.relative_to(REPO_ROOT)} (genesis written)")
    print()

    assert len(engine.list_traits()) == 29, "Expected 29 traits in v0.2 (26 from v0.1 + 3 embodiment.presentation)"
    assert set(engine.domains) == {"security", "audit", "emotional", "cognitive", "communication", "embodiment"}

    # Every trait should now have scale_mid populated.
    missing_mid = [t.name for t in engine.list_traits() if not t.scale_mid]
    assert not missing_mid, f"Traits missing scale.mid: {missing_mid}"

    # ---- one soul + constitution per role ---------------------------------
    print("Generating default soul + constitution for each role:")
    default_docs: dict[str, tuple] = {}
    for role_name, role in engine.roles.items():
        profile = engine.build_profile(role_name)
        agent_name = role_name.replace("_", " ").title().replace(" ", "")
        stem = f"{role_name}_default"
        doc, cst, soul_path, cst_path = _write_constitution_and_soul(
            gen=gen, engine=engine, profile=profile, agent_name=agent_name,
            out_dir=out_dir, stem=stem, templates_path=templates_path, audit=audit,
        )
        g = grade(profile, engine)
        hits = engine.scan_flagged(profile)
        print(
            f"  {role_name:25s}  dna={doc.dna}  "
            f"cst={cst.constitution_hash[:12]}  "
            f"grade={g.overall_score:.1f} ({g.dominant_domain})  "
            f"policies={len(cst.policies):2d}  flags={len(hits)}"
        )
        default_docs[role_name] = (doc, cst, soul_path, cst_path)
        assert verify(profile, doc.dna), f"DNA verification failed for {role_name}"
        # frontmatter binding spot check
        fm = soul_path.read_text(encoding="utf-8")
        assert f'constitution_hash: "{cst.constitution_hash}"' in fm
        assert f'constitution_file: "{cst_path.name}"' in fm

    # ---- DNA determinism check --------------------------------------------
    a = engine.build_profile("network_watcher")
    b = engine.build_profile("network_watcher")
    assert dna_short(a) == dna_short(b), "DNA is not deterministic for identical profiles"
    c = engine.build_profile("network_watcher", overrides={"caution": 50})
    assert dna_short(a) != dna_short(c), "DNA failed to change when a trait changed"

    # Constitution determinism: same profile -> same hash, regardless of agent name
    profile_nw = engine.build_profile("network_watcher")
    cst_x = build_constitution(profile_nw, engine, agent_name="X", templates_path=templates_path)
    cst_y = build_constitution(profile_nw, engine, agent_name="Y", templates_path=templates_path)
    assert cst_x.constitution_hash == cst_y.constitution_hash, \
        "constitution_hash must not depend on agent_name"

    # ---- stress test: intentional contradictory certainty ------------------
    print()
    print("Stress tests (intentional flagged combinations):")

    stress_1 = engine.build_profile(
        "network_watcher",
        overrides={"hedging": 90, "confidence": 90},
    )
    doc1, cst1, sp1, _ = _write_constitution_and_soul(
        gen=gen, engine=engine, profile=stress_1,
        agent_name="ContradictoryAgent", out_dir=out_dir,
        stem="stress_contradictory_certainty",
        templates_path=templates_path, audit=audit,
    )
    flags1 = [fc.name for fc in engine.scan_flagged(stress_1)]
    print(f"  contradictory_certainty  dna={doc1.dna}  cst={cst1.constitution_hash[:12]}  flags={flags1}")
    assert "contradictory_certainty" in flags1
    # The flagged combo must have added a forbid policy.
    forbid_ids = [p.id for p in cst1.policies if p.rule == "forbid"]
    assert any(p.source.startswith("flagged:") for p in cst1.policies), \
        "flagged combo did not produce a policy"

    stress_2 = engine.build_profile(
        "network_watcher",
        overrides={"threat_prior": 90, "suspicion": 90, "directness": 95, "empathy": 10},
    )
    doc2, cst2, sp2, _ = _write_constitution_and_soul(
        gen=gen, engine=engine, profile=stress_2,
        agent_name="EdgyWatcher", out_dir=out_dir,
        stem="stress_multiple_flags",
        templates_path=templates_path, audit=audit,
    )
    flags2 = [fc.name for fc in engine.scan_flagged(stress_2)]
    print(f"  multiple flags           dna={doc2.dna}  cst={cst2.constitution_hash[:12]}  flags={flags2}")
    assert "noisy_threat_profile" in flags2
    assert "blunt_and_cold" in flags2

    # ---- lineage demo: parent spawns child spawns grandchild --------------
    print()
    print("Lineage demo (parent -> child -> grandchild):")

    parent_profile = engine.build_profile("anomaly_investigator")
    parent_name = "HuntMaster"
    parent_doc, parent_cst, parent_path, _ = _write_constitution_and_soul(
        gen=gen, engine=engine, profile=parent_profile, agent_name=parent_name,
        out_dir=out_dir, stem="lineage_parent_huntmaster",
        templates_path=templates_path, audit=audit,
    )
    print(
        f"  parent  {parent_name:20s}  dna={parent_doc.dna}  "
        f"depth={parent_doc.lineage.depth}  cst={parent_cst.constitution_hash[:12]}"
    )

    child_profile = engine.build_profile(
        "anomaly_investigator",
        overrides={"curiosity": 85, "lateral_thinking": 80, "research_thoroughness": 95},
    )
    child_lineage = Lineage.from_parent(
        parent_dna=parent_doc.dna,
        parent_lineage=parent_doc.lineage,
        parent_agent_name=parent_name,
    )
    child_doc, child_cst, _, _ = _write_constitution_and_soul(
        gen=gen, engine=engine, profile=child_profile, agent_name="HuntMasterScout",
        out_dir=out_dir, stem="lineage_child_scout",
        templates_path=templates_path, lineage=child_lineage, audit=audit,
        audit_event_type="agent_spawned",
    )
    print(
        f"  child   {'HuntMasterScout':20s}  dna={child_doc.dna}  "
        f"depth={child_doc.lineage.depth}  parent={child_doc.lineage.parent_dna}"
    )

    assert child_doc.lineage.parent_dna == parent_doc.dna
    assert child_doc.lineage.ancestors == (parent_doc.dna,)
    assert child_doc.lineage.depth == 1
    assert child_doc.dna != parent_doc.dna, "Child must have distinct DNA"
    # NOTE: child and parent may share a constitution_hash when the child's
    # trait deltas don't cross any template threshold. That's by design —
    # constitution_hash binds the rulebook, agent DNA binds the identity.

    grandchild_profile = engine.build_profile(
        "anomaly_investigator",
        overrides={"curiosity": 95, "suspicion": 90},
    )
    grandchild_lineage = Lineage.from_parent(
        parent_dna=child_doc.dna,
        parent_lineage=child_doc.lineage,
        parent_agent_name="HuntMasterScout",
    )
    grandchild_doc, gc_cst, _, _ = _write_constitution_and_soul(
        gen=gen, engine=engine, profile=grandchild_profile, agent_name="Forager",
        out_dir=out_dir, stem="lineage_grandchild_forager",
        templates_path=templates_path, lineage=grandchild_lineage, audit=audit,
        audit_event_type="agent_spawned",
    )
    print(
        f"  g-child {'Forager':20s}  dna={grandchild_doc.dna}  "
        f"depth={grandchild_doc.lineage.depth}  chain={grandchild_doc.lineage.ancestors}"
    )

    assert grandchild_doc.lineage.depth == 2
    assert grandchild_doc.lineage.ancestors == (parent_doc.dna, child_doc.dna)

    # ---- weight sanity check ---------------------------------------------
    print()
    print("Effective-weight spot checks:")
    nw = engine.build_profile("network_watcher")
    oc = engine.build_profile("operator_companion")
    caution_nw = engine.effective_trait_weight(nw, "caution")
    caution_oc = engine.effective_trait_weight(oc, "caution")
    empathy_nw = engine.effective_trait_weight(nw, "empathy")
    empathy_oc = engine.effective_trait_weight(oc, "empathy")
    print(f"  caution in network_watcher:    {caution_nw:.2f}")
    print(f"  caution in operator_companion: {caution_oc:.2f}")
    print(f"  empathy in network_watcher:    {empathy_nw:.2f}")
    print(f"  empathy in operator_companion: {empathy_oc:.2f}")
    assert caution_nw > caution_oc, "Network watcher should weight caution more than operator companion"
    assert empathy_oc > empathy_nw, "Operator companion should weight empathy more than network watcher"

    # ---- audit chain verification -----------------------------------------
    print()
    print("Audit chain verification:")
    result = audit.verify()
    print(
        f"  ok={result.ok}  entries={result.entries_verified}  "
        f"unknown={result.unknown_event_types or '()'}"
    )
    assert result.ok, f"audit chain verification failed: {result.reason} @ seq {result.broken_at_seq}"
    # Genesis + 5 role defaults + 2 stress + 3 lineage = 11 entries
    assert result.entries_verified == 11, f"unexpected entry count: {result.entries_verified}"
    assert result.unknown_event_types == (), "no forward-compat events should appear in demo"

    print()
    print("All assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
