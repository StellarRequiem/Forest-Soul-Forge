"""Sandbox verification for the constitution builder.

Run: python3 scripts/verify_constitution.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from forest_soul_forge.core.constitution import build, STRICTNESS_ORDER  # noqa: E402
from forest_soul_forge.core.dna import dna_short  # noqa: E402
from forest_soul_forge.core.trait_engine import TraitEngine  # noqa: E402
from forest_soul_forge.soul.generator import SoulGenerator  # noqa: E402

YAML_PATH = REPO_ROOT / "config" / "trait_tree.yaml"
TEMPLATES_PATH = REPO_ROOT / "config" / "constitution_templates.yaml"


def main() -> int:
    engine = TraitEngine(YAML_PATH)
    checks: list[tuple[str, bool]] = []

    # --- every role has a template + valid derivation -------------------
    for role_name in engine.roles:
        p = engine.build_profile(role_name)
        c = build(p, engine, agent_name=f"{role_name}-test", templates_path=TEMPLATES_PATH)
        checks.append((f"{role_name}: builds", bool(c.policies)))
        checks.append((f"{role_name}: hash is 64 hex", len(c.constitution_hash) == 64))
        checks.append((f"{role_name}: policies sorted by id", [x.id for x in c.policies] == sorted(x.id for x in c.policies)))
        checks.append((f"{role_name}: dna matches", c.agent_dna == dna_short(p)))

    # --- determinism ----------------------------------------------------
    p = engine.build_profile("network_watcher")
    c1 = build(p, engine, agent_name="A", templates_path=TEMPLATES_PATH)
    c2 = build(p, engine, agent_name="A", templates_path=TEMPLATES_PATH)
    checks.append(("determinism: hash stable", c1.constitution_hash == c2.constitution_hash))
    checks.append(("determinism: yaml stable", c1.to_yaml() == c2.to_yaml()))

    # --- agent_name NOT in hash ----------------------------------------
    ca = build(p, engine, agent_name="Alpha", templates_path=TEMPLATES_PATH)
    cb = build(p, engine, agent_name="Bravo", templates_path=TEMPLATES_PATH)
    checks.append(("agent_name not in hash", ca.constitution_hash == cb.constitution_hash))

    # --- trait modifier fires on high caution ---------------------------
    pc = engine.build_profile("network_watcher", overrides={"caution": 95})
    cc = build(pc, engine, agent_name="Cautious", templates_path=TEMPLATES_PATH)
    checks.append(("high caution -> caution_high_approval added", any(x.id == "caution_high_approval" for x in cc.policies)))

    # --- flagged combo -> forbid policy --------------------------------
    if engine.flagged_combinations:
        fc = engine.flagged_combinations[0]
        overrides = {}
        for name, (op, thresh) in fc.conditions.items():
            if op == ">=": v = min(100, thresh)
            elif op == ">": v = min(100, thresh + 1)
            elif op == "<=": v = max(0, thresh)
            elif op == "<": v = max(0, thresh - 1)
            else: v = thresh
            overrides[name] = v
        pf = engine.build_profile("network_watcher", overrides=overrides)
        cf = build(pf, engine, agent_name="Flagged", templates_path=TEMPLATES_PATH)
        flagged_policies = [x for x in cf.policies if x.source == f"flagged:{fc.name}"]
        checks.append((f"flagged combo '{fc.name}' emits forbid policy", len(flagged_policies) == 1 and flagged_policies[0].rule == "forbid"))

    # --- conflict resolution: caution_high + flagged combo -------------
    if engine.flagged_combinations:
        fc = engine.flagged_combinations[0]
        overrides = {"caution": 95}
        for name, (op, thresh) in fc.conditions.items():
            if op == ">=": v = min(100, thresh)
            elif op == ">": v = min(100, thresh + 1)
            elif op == "<=": v = max(0, thresh)
            elif op == "<": v = max(0, thresh - 1)
            else: v = thresh
            overrides[name] = v
        px = engine.build_profile("network_watcher", overrides=overrides)
        cx = build(px, engine, agent_name="Conflict", templates_path=TEMPLATES_PATH)
        caution_pol = next((x for x in cx.policies if x.id == "caution_high_approval"), None)
        if caution_pol:
            checks.append(("caution_high_approval superseded by stricter forbid", caution_pol.superseded_by is not None))

    # --- non-ordered rules never superseded ----------------------------
    pic = engine.build_profile("incident_communicator")
    cic = build(pic, engine, agent_name="IC", templates_path=TEMPLATES_PATH)
    modifiers = [x for x in cic.policies if x.rule not in STRICTNESS_ORDER]
    if modifiers:
        checks.append(("modifier rules never marked superseded", all(m.superseded_by is None for m in modifiers)))

    # --- YAML is parseable + contains all sections ---------------------
    import yaml as y
    yaml_text = cic.to_yaml()
    parsed = y.safe_load(yaml_text)
    checks.append(("yaml parses back cleanly", parsed["constitution_hash"] == cic.constitution_hash))
    checks.append(("yaml lists all policies", len(parsed["policies"]) == len(cic.policies)))

    # --- soul frontmatter binds the hash -------------------------------
    gen = SoulGenerator(engine)
    soul = gen.generate(
        pic,
        agent_name="IC",
        constitution_hash=cic.constitution_hash,
        constitution_file="ic.constitution.yaml",
    )
    checks.append(("soul emits constitution_hash", f'constitution_hash: "{cic.constitution_hash}"' in soul.markdown))
    checks.append(("soul emits constitution_file", 'constitution_file: "ic.constitution.yaml"' in soul.markdown))

    # --- soul without constitution omits the fields --------------------
    soul2 = gen.generate(pic, agent_name="IC")
    checks.append(("soul without constitution: hash absent", "constitution_hash:" not in soul2.markdown))
    checks.append(("soul without constitution: file absent", "constitution_file:" not in soul2.markdown))

    # --- soul with mismatched kwargs raises ----------------------------
    try:
        gen.generate(pic, agent_name="IC", constitution_hash="deadbeef")
        checks.append(("half-constitution kwargs raise", False))
    except ValueError:
        checks.append(("half-constitution kwargs raise", True))

    # --- summary -------------------------------------------------------
    failures = [n for n, ok in checks if not ok]
    width = max(len(n) for n, _ in checks)
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}")
    print()
    print(f"{len(checks) - len(failures)}/{len(checks)} checks passed")
    if failures:
        print(f"FAILED: {failures}", file=sys.stderr)
        return 1
    print()
    print("--- Sample constitution (first 40 lines of YAML) ---")
    for line in cic.to_yaml().splitlines()[:40]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
