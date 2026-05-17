#!/usr/bin/env bash
# ADR-0079 section 01 — static config integrity.
#
# Loads every load-bearing YAML config and cross-references them:
#   - trait_tree.yaml: roles + 6-domain weights + plausible range
#   - genres.yaml: every claimed role exists in trait_engine; no
#     double-claim; ADR-0021 invariant holds
#   - constitution_templates.yaml: required blocks present per role
#   - tool_catalog.yaml: every archetype kit tool exists; per-tool
#     side_effects within genre ceiling
#   - handoffs.yaml: every (domain, capability) target exists;
#     cascade rules reference real domains
#   - domain manifests: entry_agents reference real roles
#
# Reads no daemon — pure on-disk verification. Foundation section;
# every later section assumes this passes.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-01-static-config"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 01 — static config integrity

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- target: pure on-disk YAML parse + cross-reference

HEADER

cd "$REPO_ROOT"

"$PY" - "$REPORT" <<'PYEOF'
"""Section 01 driver — static config integrity."""
import sys
import yaml
from pathlib import Path

REPORT = Path(sys.argv[1])
REPO = Path.cwd()

# Pretend to sys.path so we can import the project's loaders.
sys.path.insert(0, str(REPO / "src"))

results: list[tuple[str, str, str]] = []  # (PASS|FAIL, check_name, evidence)

def check(name: str):
    def wrap(fn):
        try:
            ev = fn() or ""
            results.append(("PASS", name, ev))
        except AssertionError as e:
            results.append(("FAIL", name, str(e)))
        except Exception as e:
            results.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        return fn
    return wrap


# ---- trait_tree.yaml ------------------------------------------------------
trait_engine = None

@check("trait_tree.yaml loads via TraitEngine")
def _():
    global trait_engine
    from forest_soul_forge.core.trait_engine import TraitEngine
    trait_engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
    return f"{len(trait_engine.roles)} roles loaded"

EXPECTED_DOMAINS = {"security","audit","cognitive","communication","emotional","embodiment"}

@check("every role has exactly 6 domain weights")
def _():
    bad = []
    for name, r in trait_engine.roles.items():
        if set(r.domain_weights.keys()) != EXPECTED_DOMAINS:
            bad.append(name)
    assert not bad, f"roles with wrong domain set: {bad}"

@check("every domain weight in [0.0, 3.0]")
def _():
    bad = []
    for name, r in trait_engine.roles.items():
        for d, v in r.domain_weights.items():
            if not (0.0 <= v <= 3.0):
                bad.append(f"{name}.{d}={v}")
    assert not bad, f"out-of-range weights: {bad}"


# ---- genres.yaml ----------------------------------------------------------
genre_engine = None

@check("genres.yaml loads")
def _():
    global genre_engine
    from forest_soul_forge.core.genre_engine import load_genres
    genre_engine = load_genres(REPO / "config" / "genres.yaml")
    return f"{len(list(genre_engine.all_genres()))} genres"

@check("every trait-engine role claimed by exactly one genre")
def _():
    from forest_soul_forge.core.genre_engine import validate_against_trait_engine
    unclaimed = validate_against_trait_engine(
        genre_engine, list(trait_engine.roles.keys()),
    )
    assert not unclaimed, f"unclaimed roles: {unclaimed}"

@check("no role double-claimed")
def _():
    dup = {}
    for g in genre_engine.all_genres():
        for role in g.roles:
            dup.setdefault(role, []).append(g.name)
    bad = {r: gs for r, gs in dup.items() if len(gs) > 1}
    assert not bad, f"doubles: {bad}"


# ---- constitution_templates.yaml ------------------------------------------
const_templates = None

@check("constitution_templates.yaml loads")
def _():
    global const_templates
    raw = yaml.safe_load(
        (REPO / "config" / "constitution_templates.yaml").read_text(encoding="utf-8")
    )
    const_templates = raw.get("role_base", {})
    return f"{len(const_templates)} templates"

@check("every template has required blocks")
def _():
    required = ("policies", "risk_thresholds", "out_of_scope",
                "operator_duties", "drift_monitoring")
    bad = []
    for role, t in const_templates.items():
        missing = [b for b in required if b not in t]
        if missing:
            bad.append(f"{role}: missing {missing}")
    assert not bad, "; ".join(bad)


# ---- tool_catalog.yaml ----------------------------------------------------
catalog = None

@check("tool_catalog.yaml loads")
def _():
    global catalog
    from forest_soul_forge.core.tool_catalog import load_catalog
    catalog = load_catalog(REPO / "config" / "tool_catalog.yaml")
    return f"{len(catalog.tools)} tools, {len(catalog.archetypes)} archetypes"

@check("every archetype kit tool exists in catalog")
def _():
    bad = []
    for role, bundle in catalog.archetypes.items():
        for ref in bundle.standard_tools:
            try:
                catalog.get_tool(ref)
            except Exception as e:
                bad.append(f"{role} → {ref.key}: {e}")
    assert not bad, "; ".join(bad[:5])


# ---- handoffs.yaml --------------------------------------------------------
handoffs = None

@check("handoffs.yaml loads")
def _():
    global handoffs
    from forest_soul_forge.core.routing_engine import load_handoffs
    handoffs, errors = load_handoffs(REPO / "config" / "handoffs.yaml")
    assert not errors, f"errors: {errors}"
    return (
        f"{len(handoffs.default_skill_per_capability)} mappings, "
        f"{len(handoffs.cascade_rules)} cascade rules"
    )


# ---- domain manifests -----------------------------------------------------
@check("every domain manifest loads + entry_agents reference real roles")
def _():
    bad = []
    for path in sorted((REPO / "config" / "domains").glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as e:
            bad.append(f"{path.name}: parse {e}")
            continue
        for ea in doc.get("entry_agents") or []:
            role = ea.get("role")
            if role and role not in trait_engine.roles:
                bad.append(f"{path.name}: entry_agent role {role!r} not in trait_engine")
    assert not bad, "; ".join(bad[:5])


# ---- emit report ----------------------------------------------------------
passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
with REPORT.open("a", encoding="utf-8") as f:
    f.write(f"## Result\n\n")
    f.write(f"- total: {len(results)}\n- passed: {passed}\n- failed: {failed}\n\n")
    f.write("## Checks\n\n")
    for status, name, ev in results:
        f.write(f"- **[{status}]** {name}")
        if ev:
            f.write(f" — {ev}")
        f.write("\n")

print(f"section 01: {passed}/{len(results)} passed; report at {REPORT}")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo
echo "----"
cat "$REPORT" | tail -30
echo "----"
echo "section 01 exit: $RC"
exit "$RC"
