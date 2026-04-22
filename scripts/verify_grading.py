"""Sandbox verification for grading engine.

pytest isn't installable in this sandbox, so this script exercises the core
paths directly with bare asserts. It's not a replacement for the real test
suite (tests/unit/test_grading.py) — just a smoke-check that the module
imports and the math is sane.

Run: python3 scripts/verify_grading.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from forest_soul_forge.core.grading import (  # noqa: E402
    CANONICAL_DOMAIN_ORDER,
    TERTIARY_MIN_VALUE,
    grade,
)
from forest_soul_forge.core.trait_engine import TraitEngine  # noqa: E402
from forest_soul_forge.core.dna import dna_short  # noqa: E402


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def main() -> int:
    yaml_path = REPO_ROOT / "config" / "trait_tree.yaml"
    engine = TraitEngine(yaml_path)

    checks: list[tuple[str, bool]] = []

    # --- default profile shape ------------------------------------------
    profile = engine.build_profile("network_watcher")
    report = grade(profile, engine)
    checks.append(("profile_dna matches dna_short", report.profile_dna == dna_short(profile)))
    checks.append(("every canonical domain present", all(d in report.per_domain for d in CANONICAL_DOMAIN_ORDER)))
    checks.append(("overall in [0,100]", 0.0 <= report.overall_score <= 100.0))
    checks.append(("schema_version is 1", report.schema_version == 1))

    # --- all max = 100 ---------------------------------------------------
    overrides_max = {t.name: 100 for t in engine.list_traits()}
    p_max = engine.build_profile("network_watcher", overrides=overrides_max)
    r_max = grade(p_max, engine)
    checks.append(("all-100 -> overall 100", approx(r_max.overall_score, 100.0)))
    checks.append(("all-100 -> every intrinsic 100", all(approx(g.intrinsic_score, 100.0) for g in r_max.per_domain.values())))

    # --- all zero = 0 ----------------------------------------------------
    overrides_zero = {t.name: 0 for t in engine.list_traits()}
    p_zero = engine.build_profile("network_watcher", overrides=overrides_zero)
    r_zero = grade(p_zero, engine)
    checks.append(("all-0 -> overall 0", approx(r_zero.overall_score, 0.0)))

    # --- uniform 50 gives 50 overall -------------------------------------
    overrides_fifty = {t.name: 50 for t in engine.list_traits()}
    p_fifty = engine.build_profile("operator_companion", overrides=overrides_fifty)
    r_fifty = grade(p_fifty, engine)
    checks.append(("all-50 -> overall 50", approx(r_fifty.overall_score, 50.0)))

    # --- weighted = intrinsic * role_weight -----------------------------
    ok = True
    for g in report.per_domain.values():
        if not approx(g.weighted_score, g.intrinsic_score * g.role_weight):
            ok = False
    checks.append(("weighted == intrinsic*role_weight", ok))

    # --- determinism -----------------------------------------------------
    a = grade(profile, engine)
    b = grade(profile, engine)
    checks.append(("deterministic", a == b))

    # --- dominant domain selection --------------------------------------
    # Zero everything except emotional.
    overrides_emo = {t.name: 0 for t in engine.list_traits()}
    for t in engine.list_traits(domain="emotional"):
        overrides_emo[t.name] = 100
    p_emo = engine.build_profile("network_watcher", overrides=overrides_emo)
    r_emo = grade(p_emo, engine)
    checks.append(("emotional-dominant -> dominant=emotional", r_emo.dominant_domain == "emotional"))

    # --- tie break to canonical first domain ----------------------------
    overrides_tie = {t.name: 50 for t in engine.list_traits()}
    dw_tie = {d: 1.0 for d in engine.domains}
    p_tie = engine.build_profile("network_watcher", overrides=overrides_tie, domain_weight_overrides=dw_tie)
    r_tie = grade(p_tie, engine)
    checks.append(("tie -> canonical first domain wins", r_tie.dominant_domain == CANONICAL_DOMAIN_ORDER[0]))

    # --- low tertiary still counts --------------------------------------
    tertiary = next((t for t in engine.list_traits() if t.tier == "tertiary"), None)
    if tertiary is not None:
        base_values = dict(profile.trait_values)
        low = dict(base_values); low[tertiary.name] = 0
        high = dict(base_values); high[tertiary.name] = 99
        p_low = engine.build_profile("network_watcher", overrides=low)
        p_high = engine.build_profile("network_watcher", overrides=high)
        r_low = grade(p_low, engine)
        r_high = grade(p_high, engine)
        diff = r_high.per_domain[tertiary.domain].intrinsic_score - r_low.per_domain[tertiary.domain].intrinsic_score
        checks.append(("low tertiary changes domain intrinsic", diff > 0))

        # skipped_traits counts tertiaries below TERTIARY_MIN_VALUE
        low_tertiary_overrides = {t.name: TERTIARY_MIN_VALUE - 1 for t in engine.list_traits() if t.tier == "tertiary"}
        p_skip = engine.build_profile("network_watcher", overrides=low_tertiary_overrides)
        r_skip = grade(p_skip, engine)
        expected_skip = {}
        for t in engine.list_traits():
            if t.tier == "tertiary":
                expected_skip[t.domain] = expected_skip.get(t.domain, 0) + 1
        ok = all(r_skip.per_domain[d].skipped_traits == c for d, c in expected_skip.items())
        checks.append(("skipped_traits counts correctly", ok))

    # --- flagged combination surfaces (if any) --------------------------
    if engine.flagged_combinations:
        fc = engine.flagged_combinations[0]
        fc_overrides = {}
        for name, (op, thresh) in fc.conditions.items():
            if op == ">=": v = min(100, thresh)
            elif op == ">": v = min(100, thresh + 1)
            elif op == "<=": v = max(0, thresh)
            elif op == "<": v = max(0, thresh - 1)
            else: v = thresh
            fc_overrides[name] = v
        p_flag = engine.build_profile("network_watcher", overrides=fc_overrides)
        r_flag = grade(p_flag, engine)
        checks.append((f"flagged combo '{fc.name}' surfaces in warnings", fc.name in r_flag.warnings))

    # --- defaults emit no warnings --------------------------------------
    for role_name in engine.roles:
        p = engine.build_profile(role_name)
        r = grade(p, engine)
        checks.append((f"defaults clean: {role_name}", r.warnings == ()))

    # --- render is non-empty and mentions role --------------------------
    rendered = report.render()
    checks.append(("render mentions role", profile.role in rendered))
    checks.append(("render mentions dna", report.profile_dna in rendered))

    # --- overall == role-weighted mean of intrinsics --------------------
    num = sum(g.intrinsic_score * g.role_weight for g in report.per_domain.values())
    den = sum(g.role_weight for g in report.per_domain.values())
    checks.append(("overall == role-weighted mean", approx(report.overall_score, num / den)))

    # --- report output --------------------------------------------------
    failures = [name for name, ok in checks if not ok]
    width = max(len(name) for name, _ in checks)
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}")
    print()
    print(f"{len(checks) - len(failures)}/{len(checks)} checks passed")

    if failures:
        print(f"FAILED: {failures}", file=sys.stderr)
        return 1

    print()
    print("--- Sample report ---")
    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
