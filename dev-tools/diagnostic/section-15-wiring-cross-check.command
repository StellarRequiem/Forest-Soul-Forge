#!/usr/bin/env bash
# ADR-0081 T1 (B394) — section-15 substrate wiring cross-check.
#
# The B363/B392 gap surfaced because catalog wiring and archetype-kit
# wiring are two different layers, and the existing 14 sections check
# each layer in isolation. Section-15 asks cross-cutting questions:
#
#   - For every cataloged tool: is it carried by at least one
#     archetype kit OR one genre_default_tools block OR one alive
#     agent constitution? If yes-catalog + no-anywhere-else, flag.
#
#   - For every installed skill: do the archetypes whose handoff
#     capability resolves to this skill carry all its required tools?
#     Skills wired in handoffs.yaml that no archetype can actually
#     run are operator-visible gaps.
#
#   - For every handoff (domain, capability) -> skill mapping: does
#     the skill exist in examples/skills? Do any of the domain's
#     entry_agents have alive instances whose constitutions carry
#     the required tools?
#
#   - Cataloged-but-orphan tools: in catalog + zero kits + zero
#     constitutions + zero skill requires. These are retirement
#     candidates OR archetype-kit assignment candidates.
#
# Outputs:
#   report.md   - operator-readable markdown summary + per-check punch lists.
#   coverage.json - structured findings for the wiring_audit.v1 skill
#                   to consume (T4) and for the umbrella's
#                   wiring-coverage.html generator (T2).
#
# Daemon-independent: reads only from on-disk config + soul_generated.
# Doesn't probe /tools/registered (that's section-04's lane); doesn't
# probe /agents (section-05's lane); section-15 is purely cross-cutting
# over the static files + the alive-agent snapshot section-05 records.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-15-wiring-cross-check"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
COVERAGE_JSON="$TARGET/coverage.json"
mkdir -p "$TARGET"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 15 — substrate wiring cross-check

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- scope: cross-cutting checks the 14 isolated-layer sections miss.
  Reads tool_catalog.yaml + handoffs.yaml + examples/skills/ +
  soul_generated/*.constitution.yaml. Daemon-independent.

HEADER

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

cd "$REPO_ROOT"

"$PY" - "$REPORT" "$COVERAGE_JSON" "$TIMESTAMP" <<'PYEOF'
"""Section 15 — substrate wiring cross-check."""
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

REPORT, COVERAGE_JSON, TIMESTAMP = sys.argv[1:4]
REPO = Path.cwd()


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# ---- inputs ----------------------------------------------------------------

catalog = _load_yaml(REPO / "config" / "tool_catalog.yaml")
handoffs = _load_yaml(REPO / "config" / "handoffs.yaml")

# Cataloged tools: top-level `tools:` map (name.vN -> dict).
cataloged_tools: set[str] = set(catalog.get("tools", {}).keys())

# Archetype kits: archetypes.<name>.standard_tools list of tool keys.
archetypes = catalog.get("archetypes") or {}
archetype_kits: dict[str, set[str]] = {
    name: set(body.get("standard_tools") or [])
    for name, body in archetypes.items()
    if isinstance(body, dict)
}

# Genre defaults: genre_default_tools.<genre> list of tool keys.
genre_defaults: dict[str, set[str]] = {
    name: set(tools or [])
    for name, tools in (catalog.get("genre_default_tools") or {}).items()
}

# B415: constitution_templates allowed_tools is ALSO a carrier source.
# tool_catalog.archetypes.<X>.standard_tools is the kit; but
# constitution_templates.role_base.<X>.allowed_tools widens the per-
# role allowance (used by domain_orchestrator + companion roles that
# don't carry the tool in their standard kit but DO permit it at the
# constitution layer). Without this carrier source, decompose_intent.v1
# / route_to_domain.v1 / operator_profile_*.v1 show as orphan even
# though they're operationally wired into domain_orchestrator's
# constitution.
template_allows: dict[str, set[str]] = defaultdict(set)
try:
    ct_doc = _load_yaml(REPO / "config" / "constitution_templates.yaml")
    rb = (ct_doc.get("role_base") or {}) if isinstance(ct_doc, dict) else {}
    for role_name, role_body in rb.items():
        if not isinstance(role_body, dict):
            continue
        for tool_key in role_body.get("allowed_tools") or []:
            template_allows[str(tool_key)].add(role_name)
except Exception:
    pass

# Agent constitutions: soul_generated/*.constitution.yaml carries a
# `tools:` list of {name, version, ...}. Build a map of
# tool_key -> set(agent_name) so we can report carriers per tool.
constitution_dir = REPO / "soul_generated"
agent_carries: dict[str, set[str]] = defaultdict(set)
broken_constitutions: list[tuple[str, str]] = []
if constitution_dir.exists():
    for path in sorted(constitution_dir.glob("*.constitution.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            broken_constitutions.append((path.name, str(e)))
            continue
        agent_name = (doc.get("agent") or {}).get("agent_name") or path.stem
        for t in doc.get("tools") or []:
            if not isinstance(t, dict):
                continue
            n = t.get("name")
            v = t.get("version", "1")
            if n:
                agent_carries[f"{n}.v{v}"].add(agent_name)

# Skill manifests: examples/skills/*.yaml carries requires list.
skill_dir = REPO / "examples" / "skills"
skills: dict[str, dict] = {}  # key -> {name, version, requires}
if skill_dir.exists():
    for path in sorted(skill_dir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        name = doc.get("name")
        version = str(doc.get("version", "1"))
        if not name:
            continue
        requires = doc.get("requires") or []
        if not isinstance(requires, list):
            continue
        skills[f"{name}.v{version}"] = {
            "name": name,
            "version": version,
            "requires": [str(r) for r in requires],
        }

# Handoffs: handoffs.default_skill_per_capability list of mappings.
handoff_routes: list[dict] = list(
    handoffs.get("default_skill_per_capability") or []
)


# ---- checks ----------------------------------------------------------------

results: list[tuple[str, str, str]] = []  # status, check_name, evidence


# 1. Tool wiring coverage: catalog ↔ kits ↔ constitutions.
orphan_tools: list[str] = []
kit_only_tools: list[str] = []   # in kit but no agent carries
all_carrier_archetypes: dict[str, set[str]] = defaultdict(set)
for name, tools in archetype_kits.items():
    for t in tools:
        all_carrier_archetypes[t].add(name)
for name, tools in genre_defaults.items():
    for t in tools:
        all_carrier_archetypes[t].add(f"(genre:{name})")
# B415: constitution_templates allowed_tools count too. Domain
# orchestrator and similar roles permit tools at the constitution
# layer without including them in their standard archetype kit.
for tool_key, roles in template_allows.items():
    for r in roles:
        all_carrier_archetypes[tool_key].add(f"(allowed:{r})")

for tool_key in sorted(cataloged_tools):
    in_kit = tool_key in all_carrier_archetypes
    in_agent = bool(agent_carries.get(tool_key))
    if not in_kit and not in_agent:
        orphan_tools.append(tool_key)
    elif in_kit and not in_agent:
        kit_only_tools.append(tool_key)

if orphan_tools:
    results.append((
        "FAIL",
        f"tool wiring coverage ({len(orphan_tools)} orphan tools)",
        "; ".join(orphan_tools[:10]) + ("..." if len(orphan_tools) > 10 else "")
        + " — in catalog, zero archetypes/agents carry them",
    ))
else:
    results.append((
        "PASS",
        "tool wiring coverage",
        f"all {len(cataloged_tools)} cataloged tools have at least one "
        f"archetype/genre/agent carrier",
    ))

# kit_only_tools is INFO not FAIL — having tools in kits before any
# agent is born is normal during rollouts.
results.append((
    "INFO" if kit_only_tools else "PASS",
    "tools in archetype kits but no alive agent yet",
    f"{len(kit_only_tools)} tool(s): "
    + (", ".join(kit_only_tools[:8]) + ("..." if len(kit_only_tools) > 8 else "")
       if kit_only_tools else "none"),
))


# 2. Skill wiring coverage: every skill's requires resolve in catalog,
#    AND at least one archetype can carry all the requires.
skill_unresolvable: list[tuple[str, list[str]]] = []   # required not in catalog
skill_no_carrier: list[tuple[str, list[str]]] = []     # required in catalog but no archetype kit carries all

for skill_key, sd in skills.items():
    missing_from_catalog = [
        r for r in sd["requires"] if r not in cataloged_tools
    ]
    if missing_from_catalog:
        skill_unresolvable.append((skill_key, missing_from_catalog))
        continue
    # Find archetypes that carry ALL required tools.
    archetypes_that_can = [
        name for name, kit in archetype_kits.items()
        if all(r in kit or r in all_carrier_archetypes for r in sd["requires"])
    ]
    if not archetypes_that_can:
        skill_no_carrier.append((skill_key, sd["requires"]))

if skill_unresolvable:
    results.append((
        "FAIL",
        f"skill requires resolve in catalog ({len(skill_unresolvable)} unresolved)",
        "; ".join(f"{k}: missing {sorted(missing)}" for k, missing in skill_unresolvable[:5]),
    ))
else:
    results.append((
        "PASS",
        "skill requires resolve in catalog",
        f"all {len(skills)} installed skills have their requires "
        f"resolvable to cataloged tools",
    ))

if skill_no_carrier:
    results.append((
        "FAIL",
        f"skills runnable by some archetype ({len(skill_no_carrier)} unreachable)",
        "; ".join(k for k, _ in skill_no_carrier[:5])
        + " — installed but no archetype kit carries all required tools",
    ))
else:
    results.append((
        "PASS",
        "every installed skill has at least one carrier archetype",
        f"all {len(skills)} skills runnable by at least one archetype kit",
    ))


# 3. Handoff resolution: skill exists + entry_agents carry required tools.
# B415: handoffs.yaml routes can carry `future_skill: true` to mark
# a route as intentionally-declared-ahead-of-skill. Those are not
# broken — the dispatcher returns a clean 'skill not found' until
# the skill lands. We bucket them as INFO not FAIL.
handoff_broken: list[tuple[str, str, str]] = []  # domain, capability, reason
handoff_future: list[tuple[str, str, str]] = []  # routes intentionally ahead of skill

# Load each domain's entry_agents from config/domains/.
domain_dir = REPO / "config" / "domains"
domain_entry_agents: dict[str, list[str]] = {}
if domain_dir.exists():
    for path in sorted(domain_dir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        did = doc.get("domain_id")
        if not did:
            continue
        domain_entry_agents[did] = [
            ea.get("role") for ea in (doc.get("entry_agents") or [])
            if ea.get("role")
        ]

for route in handoff_routes:
    domain = route.get("domain")
    cap = route.get("capability")
    skill_name = route.get("skill_name")
    skill_ver = str(route.get("skill_version", "1"))
    if not (domain and cap and skill_name):
        continue
    skill_key = f"{skill_name}.v{skill_ver}"
    sd = skills.get(skill_key)
    if not sd:
        # B415: future_skill: true means operator declared the route
        # ahead of the skill artifact landing. Surface as INFO not FAIL.
        if route.get("future_skill") is True:
            handoff_future.append((
                domain, cap,
                f"skill {skill_key} intentionally ahead of artifact"
            ))
        else:
            handoff_broken.append((
                domain, cap,
                f"skill {skill_key} not in examples/skills/"
            ))
        continue
    # Find an entry_agent role that carries all required tools.
    entry_roles = domain_entry_agents.get(domain, [])
    if not entry_roles:
        # Handoff exists but domain has no entry_agents — section-09's
        # lane. Skip here.
        continue
    runnable_roles = [
        role for role in entry_roles
        if role in archetype_kits
        and all(r in archetype_kits[role] for r in sd["requires"])
    ]
    if not runnable_roles:
        handoff_broken.append((
            domain, cap,
            f"no entry_agent role in {domain} carries all required tools "
            f"({sd['requires']})"
        ))

if handoff_broken:
    results.append((
        "FAIL",
        f"handoff routes resolve end-to-end ({len(handoff_broken)} broken)",
        "; ".join(
            f"{d}/{c}: {reason[:80]}"
            for d, c, reason in handoff_broken[:5]
        ),
    ))
else:
    results.append((
        "PASS",
        "handoff routes resolve end-to-end",
        f"all {len(handoff_routes)} (domain, capability) -> skill "
        f"mappings backed by at least one runnable role",
    ))


# 4. Broken constitutions count — for situational awareness.
if broken_constitutions:
    results.append((
        "INFO",
        f"constitution parse health",
        f"{len(broken_constitutions)} constitution YAML files failed "
        f"to parse — see section-05 quarantine for details",
    ))


# ---- write structured JSON for the sentinel skill -------------------------

coverage = {
    "timestamp": TIMESTAMP,
    "summary": {
        "tools_total":         len(cataloged_tools),
        "tools_orphan":        len(orphan_tools),
        "tools_kit_no_agent":  len(kit_only_tools),
        "skills_total":        len(skills),
        "skills_unresolvable": len(skill_unresolvable),
        "skills_no_carrier":   len(skill_no_carrier),
        "handoffs_total":      len(handoff_routes),
        "handoffs_broken":     len(handoff_broken),
        "broken_constitutions": len(broken_constitutions),
    },
    "orphan_tools":   sorted(orphan_tools),
    "kit_only_tools": sorted(kit_only_tools),
    "skills_unresolvable": [
        {"skill": k, "missing_from_catalog": sorted(m)}
        for k, m in skill_unresolvable
    ],
    "skills_no_carrier": [
        {"skill": k, "requires": list(r)}
        for k, r in skill_no_carrier
    ],
    "handoffs_broken": [
        {"domain": d, "capability": c, "reason": r}
        for d, c, r in handoff_broken
    ],
    # Per-tool carrier matrix (operator drilldown).
    "tool_carriers": {
        tool_key: {
            "archetypes": sorted(all_carrier_archetypes.get(tool_key, set())),
            "agents": sorted(agent_carries.get(tool_key, set())),
        }
        for tool_key in sorted(cataloged_tools)
    },
}

with open(COVERAGE_JSON, "w", encoding="utf-8") as f:
    json.dump(coverage, f, indent=2)


# ---- emit markdown report -------------------------------------------------

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
info = sum(1 for r in results if r[0] == "INFO")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total checks: {len(results)}\n"
            f"- passed: {passed}\n- failed: {failed}\n- info: {info}\n\n"
            f"Structured findings: `{COVERAGE_JSON.split('/')[-1]}`\n\n## Checks\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n} — {ev}\n")
    f.write("\n## Coverage summary\n\n")
    f.write(f"- Cataloged tools: {len(cataloged_tools)}\n")
    f.write(f"  - Carried by ≥1 archetype kit OR alive agent: "
            f"{len(cataloged_tools) - len(orphan_tools)}\n")
    f.write(f"  - Orphan (zero carriers): {len(orphan_tools)}\n")
    f.write(f"  - In kit but no alive agent yet: {len(kit_only_tools)}\n")
    f.write(f"- Installed skills: {len(skills)}\n")
    f.write(f"  - Unresolvable (missing from catalog): {len(skill_unresolvable)}\n")
    f.write(f"  - No carrier archetype: {len(skill_no_carrier)}\n")
    f.write(f"- Handoff routes: {len(handoff_routes)}\n")
    f.write(f"  - Broken end-to-end: {len(handoff_broken)}\n")
    f.write(f"- Archetype kits: {len(archetype_kits)}\n")
    f.write(f"- Genre defaults: {len(genre_defaults)}\n")
    f.write(f"- Domain entry maps: {len(domain_entry_agents)}\n")
    f.write(f"- Alive agents (with parseable constitutions): "
            f"{len(set().union(*agent_carries.values())) if agent_carries else 0}\n")

print(f"section 15: {passed}/{len(results)} pass ({failed} fail, {info} info)")
print(f"coverage JSON: {COVERAGE_JSON}")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -30
echo "----"
echo "section 15 exit: $RC"
exit "$RC"
