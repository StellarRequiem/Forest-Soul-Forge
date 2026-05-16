#!/bin/bash
# Burst 334 - ADR-0077 T3: D4 handoffs.yaml wiring + integration tests.
#
# Three new skill mappings land in handoffs.yaml. The d4.review_
# signoff → d8.compliance_scan cascade already exists from
# ADR-0067 T4 — preserved. The d4.release_gating → d1.index_
# artifact cascade declared in ADR-0077 §D3 is DEFERRED until
# D1 rolls out (D1 currently status='planned'); when D1 ships,
# the cascade rule lands in that rollout's T3 burst.
#
# What ships:
#
# 1. config/handoffs.yaml:
#    Three new entries under default_skill_per_capability:
#      d4_code_review.test_proposal    → propose_tests.v1
#      d4_code_review.migration_safety → safe_migration.v1
#      d4_code_review.release_gating   → release_check.v1
#    The skills themselves don't exist yet (T4 = B335-B337);
#    the dispatcher discovers the missing skill at execute time
#    and returns a clean error, which is the right behavior
#    during the T2b → T4 window.
#
# 2. config/domains/d4_code_review.yaml:
#    entry_agents extended with the three new (role, capability)
#    pairs from ADR-0077 T2. Pre-birth state surfaces as
#    UNROUTABLE_NO_ALIVE_AGENT in the routing engine — the
#    operator-visible signal that birth scripts haven't run yet.
#
# 3. tests/unit/test_d4_handoffs_wiring.py (NEW):
#    18 cases covering structural integrity, manifest entry_agents,
#    resolve_route happy + pre-birth paths, cascade behavior
#    (regression + planned-domain refusal), and the terminal-by-
#    design invariant for the three new capabilities.
#
# Sandbox-verified 18/18 new + 0 regressions across the D4 +
# routing suites (61/61 total).
#
# === ADR-0077 progress: T1 + T2 + T2b + T3 ===
# Substrate complete. Routing rail can dispatch to the three new
# capabilities the moment the birth scripts run. Next: T4 skill
# implementations (B335-B337) — propose_tests.v1, safe_migration.v1,
# release_check.v1.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/handoffs.yaml \
        config/domains/d4_code_review.yaml \
        tests/unit/test_d4_handoffs_wiring.py \
        dev-tools/commit-bursts/commit-burst334-adr0077-d4-handoffs-wiring.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d4): ADR-0077 T3 - handoffs.yaml wiring + tests (B334)

Burst 334. Three new (domain, capability) → skill mappings land
in handoffs.yaml. The d4.review_signoff → d8.compliance_scan
cascade from ADR-0067 T4 is preserved. The d4.release_gating →
d1.index_artifact cascade declared in ADR-0077 §D3 is deferred
until D1 rolls out (currently status='planned').

What ships:

  - config/handoffs.yaml: three new entries under default_skill_
    per_capability — d4.test_proposal → propose_tests.v1,
    d4.migration_safety → safe_migration.v1, d4.release_gating
    → release_check.v1. The skills themselves don't exist yet
    (T4 = B335-B337); the dispatcher discovers the missing
    skill at execute time and returns a clean error, which is
    the right behavior during the T2b → T4 window.

  - config/domains/d4_code_review.yaml: entry_agents extended
    with the three new (role, capability) pairs from T2. Pre-
    birth state surfaces as UNROUTABLE_NO_ALIVE_AGENT in the
    routing engine — the operator-visible signal that birth
    scripts haven't run yet.

  - tests/unit/test_d4_handoffs_wiring.py: 18 cases covering
    structural integrity (3 parametrized new mappings + 1
    pre-existing-mappings regression + 1 cascade regression),
    manifest entry_agents (3 parametrized new pairs + 1
    original-triune regression), resolve_route happy path with
    advanced agents alive (3 parametrized), resolve_route pre-
    birth path (3 parametrized), cascade behavior (1 live + 1
    planned + 1 terminal-by-design invariant).

Sandbox-verified 18/18 new + 0 regressions across D4 + routing
suites (61/61 total).

ADR-0077 progress: T1 + T2 + T2b + T3 shipped. Substrate
complete. Next: T4 skill implementations (B335-B337) —
propose_tests.v1, safe_migration.v1, release_check.v1."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 334 complete - ADR-0077 T3 handoffs wiring shipped ==="
echo "Substrate complete. Birth + dispatch path ready end-to-end."
echo ""
echo "Press any key to close."
read -n 1
