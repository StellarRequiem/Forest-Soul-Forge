#!/bin/bash
# Burst 329 - ADR-0072 T4: orchestrator integration of bias layers.
#
# Wraps the hardcoded routing rail with the operator-preference +
# active-learned-rule bias that ADR-0072 D1's precedence ladder
# specifies. Hardcoded handoffs always win (they're checked first
# in resolve_route); preferences (tier 400) and active learned
# rules (tier 100) kick in ONLY when decompose_intent flagged the
# sub-intent as 'ambiguous'.
#
# What ships:
#
# 1. src/forest_soul_forge/core/behavior_routing.py (NEW):
#    - BiasApplication dataclass — carries layer + rule_id +
#      statement + target_domain for audit-chain provenance.
#    - apply_behavior_bias(subintent, preferences, learned_rules)
#      → (biased_subintent, applied_or_None). Walks preferences
#      first (highest weight wins, ties break on id), falls back
#      to active learned rules. Pending + refused rules are
#      ignored — only RA-verified active rules apply. Weight=0
#      entries are treated as "off". Pure function: never
#      mutates inputs.
#    - annotate_route_with_bias(route, applied): stamps the bias
#      trace onto ResolvedRoute.reason as "via preference 'id':
#      statement | original_reason" so the audit chain captures
#      which non-hardcoded layer drove the routing decision.
#
# Tests (test_behavior_routing.py - 18 cases):
#   apply_behavior_bias (13):
#     routable passes through, planned/no_match/unknown all pass
#     through (parametrized 3×), ambiguous + matching preference
#     rewrites, input not mutated, higher-weight preference wins,
#     weight-0 preference ignored, weight ties break on id,
#     ambiguous + active rule only, preference wins over learned
#     rule, pending rules ignored, refused rules ignored, empty
#     pools unchanged
#   annotate_route_with_bias (4):
#     no bias passthrough, unroutable + bias passthrough,
#     preference bias prepends reason, learned bias prepends reason
#
# Sandbox-verified 18/18 pass.
#
# === ADR-0072 progress: T4 shipped — 4/5 closed ===
# Tranche scorecard: T1 substrate (B290) + T2 fsf provenance CLI
# (B303) + T3 RA cron (B325) + T4 orchestrator bias (B329, this).
# Only T5 frontend Provenance pane remains.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/behavior_routing.py \
        tests/unit/test_behavior_routing.py \
        dev-tools/commit-bursts/commit-burst329-adr0072-t4-orchestrator-bias.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(provenance): ADR-0072 T4 - orchestrator bias from preferences + learned rules (B329)

Burst 329. Wraps the hardcoded routing rail (ADR-0067 T4) with
the operator-preference + active-learned-rule bias that ADR-0072
D1's precedence ladder specifies. Hardcoded handoffs always win;
preferences (tier 400) and active learned rules (tier 100) kick
in ONLY when decompose_intent flagged a sub-intent as 'ambiguous'.

What ships:

  - core/behavior_routing.py (NEW): BiasApplication dataclass
    + apply_behavior_bias(subintent, preferences, learned_rules)
    pure function. Walks preferences first (highest weight wins,
    ties break on id for determinism), falls back to active
    learned rules. Pending + refused rules are ignored — only
    RA-verified active rules apply per ADR-0072 D2. Weight=0 is
    'off'. Inputs never mutated. annotate_route_with_bias stamps
    the bias trace onto ResolvedRoute.reason as 'via preference
    /id/: statement | original_reason' so the audit chain
    captures which non-hardcoded layer drove the routing
    decision.

Tests: test_behavior_routing.py — 18 cases covering 13 apply_
behavior_bias branches (routable/planned/no_match/unknown all
pass through, ambiguous+preference rewrites, immutability,
higher-weight wins, weight-0 ignored, ties break on id, active-
rule only, preference beats learned, pending+refused ignored,
empty pools unchanged) and 4 annotate cases (no-bias and
unroutable passthrough, preference + learned reason
prepending). Sandbox-verified 18/18 pass.

ADR-0072 progress: 4/5 closed (T1 substrate + T2 CLI + T3 RA
cron + T4 orchestrator bias). Only T5 frontend Provenance pane
remains — Phase α 10/10 closure in B330."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 329 complete - ADR-0072 T4 orchestrator bias shipped ==="
echo "ADR-0072: 4/5 tranches closed. Phase alpha: 8/10."
echo ""
echo "Press any key to close."
read -n 1
