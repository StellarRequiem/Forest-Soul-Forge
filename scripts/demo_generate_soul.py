#!/usr/bin/env python3
"""End-to-end smoke test + demo.

Loads the trait tree, builds one profile per role, generates a soul.md for each,
writes them to examples/, and also generates:
  - two "stress test" profiles with intentional overrides to show flagged output
  - one parent + child + grandchild chain demonstrating lineage inheritance

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

from forest_soul_forge.core.trait_engine import TraitEngine  # noqa: E402
from forest_soul_forge.soul.dna import Lineage, dna_short, verify  # noqa: E402
from forest_soul_forge.soul.generator import SoulGenerator  # noqa: E402


def main() -> int:
    yaml_path = REPO_ROOT / "config" / "trait_tree.yaml"
    out_dir = REPO_ROOT / "examples"
    out_dir.mkdir(exist_ok=True)

    engine = TraitEngine(yaml_path)
    gen = SoulGenerator(engine)

    print(f"Trait tree v{engine.version}")
    print(f"  domains:   {len(engine.domains)}")
    print(f"  traits:    {len(engine.list_traits())}")
    print(f"  roles:     {len(engine.roles)}")
    print(f"  flagged:   {len(engine.flagged_combinations)}")
    print()

    assert len(engine.list_traits()) == 26, "Expected 26 traits in v0.1"
    assert set(engine.domains) == {"security", "audit", "emotional", "cognitive", "communication"}

    # Every trait should now have scale_mid populated.
    missing_mid = [t.name for t in engine.list_traits() if not t.scale_mid]
    assert not missing_mid, f"Traits missing scale.mid: {missing_mid}"

    # ---- one soul per role -------------------------------------------------
    print("Generating default soul.md for each role:")
    for role_name, role in engine.roles.items():
        profile = engine.build_profile(role_name)
        agent_name = role_name.replace("_", " ").title().replace(" ", "")
        doc = gen.generate(profile, agent_name=agent_name)
        path = out_dir / f"{role_name}_default.soul.md"
        doc.write(path)
        hits = engine.scan_flagged(profile)
        print(f"  {role_name:25s}  ->  {path.relative_to(REPO_ROOT)}  dna={doc.dna}  flags={len(hits)}")
        assert verify(profile, doc.dna), f"DNA verification failed for {role_name}"

    # ---- DNA determinism check --------------------------------------------
    a = engine.build_profile("network_watcher")
    b = engine.build_profile("network_watcher")
    assert dna_short(a) == dna_short(b), "DNA is not deterministic for identical profiles"
    c = engine.build_profile("network_watcher", overrides={"caution": 50})
    assert dna_short(a) != dna_short(c), "DNA failed to change when a trait changed"

    # ---- stress test: intentional contradictory certainty ------------------
    print()
    print("Stress tests (intentional flagged combinations):")

    stress_1 = engine.build_profile(
        "network_watcher",
        overrides={"hedging": 90, "confidence": 90},
    )
    doc1 = gen.generate(stress_1, agent_name="ContradictoryAgent")
    path1 = out_dir / "stress_contradictory_certainty.soul.md"
    doc1.write(path1)
    flags1 = [fc.name for fc in engine.scan_flagged(stress_1)]
    print(f"  contradictory_certainty  ->  {path1.relative_to(REPO_ROOT)}  dna={doc1.dna}  flags={flags1}")
    assert "contradictory_certainty" in flags1

    stress_2 = engine.build_profile(
        "network_watcher",
        overrides={"threat_prior": 90, "suspicion": 90, "directness": 95, "empathy": 10},
    )
    doc2 = gen.generate(stress_2, agent_name="EdgyWatcher")
    path2 = out_dir / "stress_multiple_flags.soul.md"
    doc2.write(path2)
    flags2 = [fc.name for fc in engine.scan_flagged(stress_2)]
    print(f"  multiple flags           ->  {path2.relative_to(REPO_ROOT)}  dna={doc2.dna}  flags={flags2}")
    assert "noisy_threat_profile" in flags2
    assert "blunt_and_cold" in flags2

    # ---- lineage demo: parent spawns child spawns grandchild --------------
    print()
    print("Lineage demo (parent -> child -> grandchild):")

    parent_profile = engine.build_profile("anomaly_investigator")
    parent_name = "HuntMaster"
    parent_doc = gen.generate(parent_profile, agent_name=parent_name)
    parent_path = out_dir / "lineage_parent_huntmaster.soul.md"
    parent_doc.write(parent_path)
    print(f"  parent  {parent_name:20s}  dna={parent_doc.dna}  depth={parent_doc.lineage.depth}")

    child_profile = engine.build_profile(
        "anomaly_investigator",
        overrides={"curiosity": 85, "lateral_thinking": 80, "research_thoroughness": 95},
    )
    child_lineage = Lineage.from_parent(
        parent_dna=parent_doc.dna,
        parent_lineage=parent_doc.lineage,
        parent_agent_name=parent_name,
    )
    child_doc = gen.generate(
        child_profile,
        agent_name="HuntMasterScout",
        lineage=child_lineage,
    )
    child_path = out_dir / "lineage_child_scout.soul.md"
    child_doc.write(child_path)
    print(f"  child   {'HuntMasterScout':20s}  dna={child_doc.dna}  depth={child_doc.lineage.depth}  parent={child_doc.lineage.parent_dna}")

    assert child_doc.lineage.parent_dna == parent_doc.dna
    assert child_doc.lineage.ancestors == (parent_doc.dna,)
    assert child_doc.lineage.depth == 1
    assert child_doc.dna != parent_doc.dna, "Child must have distinct DNA"

    grandchild_profile = engine.build_profile(
        "anomaly_investigator",
        overrides={"curiosity": 95, "suspicion": 90},
    )
    grandchild_lineage = Lineage.from_parent(
        parent_dna=child_doc.dna,
        parent_lineage=child_doc.lineage,
        parent_agent_name="HuntMasterScout",
    )
    grandchild_doc = gen.generate(
        grandchild_profile,
        agent_name="Forager",
        lineage=grandchild_lineage,
    )
    grandchild_path = out_dir / "lineage_grandchild_forager.soul.md"
    grandchild_doc.write(grandchild_path)
    print(f"  g-child {'Forager':20s}  dna={grandchild_doc.dna}  depth={grandchild_doc.lineage.depth}  chain={grandchild_doc.lineage.ancestors}")

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

    print()
    print("All assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
