#!/usr/bin/env bash
# ADR-0079 section 09 — handoff routing.
#
# Pure on-disk cross-reference (no daemon required):
#   1. Every (domain, capability) mapping in handoffs.yaml
#      points at a domain that exists in config/domains/
#   2. Every domain's entry_agents reference roles that exist
#      in trait_tree.yaml AND are claimed by some genre
#   3. Cascade rules: source_domain + target_domain both exist;
#      target_capability is in target_domain's capabilities list
#      OR target_domain.status='planned' (deferred wiring OK)
#   4. handoffs.yaml's default_skill_per_capability covers every
#      capability declared in every domain manifest, OR the
#      capability has no consumer mapping declared yet (expected
#      for domains still in early rollout)

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-09-handoff-routing"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 09 — handoff routing

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- scope: pure on-disk (no daemon)

HEADER

cd "$REPO_ROOT"
PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

"$PY" - "$REPORT" <<'PYEOF'
"""Section 09 — handoff routing cross-reference."""
import sys
from pathlib import Path

import yaml

REPORT = Path(sys.argv[1])
REPO = Path.cwd()
sys.path.insert(0, str(REPO / "src"))

from forest_soul_forge.core.routing_engine import load_handoffs
from forest_soul_forge.core.trait_engine import TraitEngine
from forest_soul_forge.core.genre_engine import load_genres

trait_engine = TraitEngine(REPO / "config" / "trait_tree.yaml")
genres = load_genres(REPO / "config" / "genres.yaml")
handoffs, errors = load_handoffs(REPO / "config" / "handoffs.yaml")
assert not errors, f"handoffs load errors: {errors}"

# Load every domain manifest into a dict by domain_id.
domains: dict[str, dict] = {}
for path in sorted((REPO / "config" / "domains").glob("*.yaml")):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    did = doc.get("domain_id")
    if did:
        domains[did] = doc

trait_roles = set(trait_engine.roles.keys())
claimed_roles = set()
for g in genres.all_genres():
    for r in g.roles:
        claimed_roles.add(r)

results: list[tuple[str, str, str]] = []

# Check 1: every (domain, capability) mapping points at an existing domain
bad = []
for (dom, cap) in handoffs.default_skill_per_capability:
    if dom not in domains:
        bad.append(f"({dom}, {cap}) → unknown domain")
if bad:
    results.append(("FAIL", "handoffs map to known domains", "; ".join(bad[:5])))
else:
    results.append(("PASS", "handoffs map to known domains",
                    f"{len(handoffs.default_skill_per_capability)} mappings"))

# Check 2: every entry_agent role exists in trait_engine AND is claimed by a genre
# B365 — planned domains are intentionally upstream of their rollout
# arc (ADR-0067 dependency order: D4 -> D3 -> D8 -> D1 -> D2 -> D7 ->
# D9 -> D10 -> D5 -> D6). Their entry_agents may reference roles
# whose wiring lands when the arc begins. Strict checking would
# force premature wiring; skip planned domains for this check and
# rely on the planned-domain catalogue in section 01 for visibility.
bad = []
planned_deferred = []
for did, doc in domains.items():
    if (doc.get("status") or "").lower() == "planned":
        unlanded = [
            ea.get("role") for ea in (doc.get("entry_agents") or [])
            if ea.get("role") and ea.get("role") not in trait_roles
        ]
        if unlanded:
            planned_deferred.append(f"{did}: {sorted(set(unlanded))}")
        continue
    for ea in doc.get("entry_agents") or []:
        role = ea.get("role")
        if role and role not in trait_roles:
            bad.append(f"{did}: role {role!r} not in trait_engine")
        elif role and role not in claimed_roles:
            bad.append(f"{did}: role {role!r} not claimed by any genre")
if bad:
    results.append(("FAIL", "entry_agents reference real claimed roles",
                    "; ".join(bad[:5])))
else:
    n = sum(len(d.get("entry_agents") or []) for d in domains.values())
    suffix = ""
    if planned_deferred:
        suffix = f"; deferred (planned domains): {'; '.join(planned_deferred)}"
    results.append(("PASS", "entry_agents reference real claimed roles",
                    f"{n} entry_agents across {len(domains)} domains{suffix}"))

# Check 3: cascade rules — both domains exist, target_capability is in target_domain
bad = []
for rule in handoffs.cascade_rules:
    sd = rule.source_domain
    td = rule.target_domain
    tc = rule.target_capability
    if sd not in domains:
        bad.append(f"cascade source_domain {sd!r} unknown")
        continue
    if td not in domains:
        bad.append(f"cascade target_domain {td!r} unknown")
        continue
    target_caps = set(domains[td].get("capabilities") or [])
    target_status = domains[td].get("status", "unknown")
    if tc not in target_caps and target_status != "planned":
        bad.append(
            f"cascade {sd}.{rule.source_capability} → {td}.{tc}: "
            f"target_capability not in target's capabilities (status={target_status})"
        )
if bad:
    results.append(("FAIL", "cascade rules resolve cleanly",
                    "; ".join(bad[:5])))
else:
    results.append(("PASS", "cascade rules resolve cleanly",
                    f"{len(handoffs.cascade_rules)} rules"))

# Check 4 (informational): every domain capability has a handoffs mapping
# OR the capability is declared but no consumer is wired yet (acceptable —
# means the orchestrator can't route it via decompose_intent until a mapping
# lands, but the domain knows about it for planning purposes).
unmapped = []
mapped_keys = set(handoffs.default_skill_per_capability.keys())
for did, doc in domains.items():
    for cap in doc.get("capabilities") or []:
        if (did, cap) not in mapped_keys:
            unmapped.append(f"{did}.{cap}")
# Report as INFO not FAIL — gaps are expected during a rollout.
if unmapped:
    results.append(("INFO", "domain capabilities without handoff mapping",
                    f"{len(unmapped)} unmapped: "
                    + ", ".join(unmapped[:8])
                    + ("..." if len(unmapped) > 8 else "")))
else:
    results.append(("PASS", "every domain capability has a handoff mapping",
                    f"{sum(len(d.get('capabilities') or []) for d in domains.values())} capabilities"))

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
info = sum(1 for r in results if r[0] == "INFO")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)}\n"
            f"- passed: {passed}\n- failed: {failed}\n- info: {info}\n\n## Checks\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n} — {ev}\n")

print(f"section 09: {passed}/{len(results)} passed, {info} info, {failed} failed")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -25
echo "----"
echo "section 09 exit: $RC"
exit "$RC"
