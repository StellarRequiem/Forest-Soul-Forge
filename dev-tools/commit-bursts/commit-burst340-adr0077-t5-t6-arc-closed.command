#!/bin/bash
# Burst 340 - ADR-0077 T5 + T6 — CLOSES ADR-0077 6/6.
#
# Final D4 advanced rollout burst. Two artifacts ship:
#
# 1. dev-tools/birth-d4-advanced.command (NEW, T5):
#    Operator-driven umbrella that runs the three individual
#    birth scripts in the recommended order — test_author
#    (cheapest, no apply gate; observe approval queue first) →
#    release_gatekeeper (advisory-only, safe early) →
#    migration_pilot (most cautious, birth last). Each
#    individual script is itself idempotent; the umbrella
#    inherits that property. Aborts the chain on any
#    individual failure with a clear "this many succeeded,
#    this one failed" message. Operator-runnable from Finder.
#
# 2. docs/runbooks/d4-advanced-rollout.md (NEW, T6):
#    End-to-end operator runbook. Sections cover one-time
#    setup (daemon restart, umbrella birth, skill install via
#    Skill Forge UI or operator-direct cp), dispatch flow with
#    three worked examples (test-first discipline, safe
#    migration, release gating), observation (agent identity
#    artifacts, audit chain filtering, approval queue),
#    recovery for 6 common failure modes (unknown role,
#    AgentKeyStoreError, python-multipart ImportError,
#    narrow-kit pre-B336 agent, migration rollback,
#    PASS-with-prose verdict), and explicit out-of-scope list
#    (auto-promote learned rules, fsf migrate apply CLI, kit
#    rebuild for existing agents, d8 cascade until D8 rolls
#    out).
#
# T5 SBOM ownership decision: SBOM generation stays in
# dev-tools/generate-sbom.command (existing, operator-driven).
# release_gatekeeper does NOT own SBOM generation — adding it
# would scope-creep T4c retroactively. The drift sentinel
# (`./dev-tools/check-drift.sh`) already invoked by
# release_check.v1's drift_sentinel step can be extended to
# verify SBOM presence in a future tranche if operator wants;
# defer until concrete pain surfaces.
#
# === ADR-0077 CLOSED 6/6 ===
# T1 doc (B331) + T2 roles (B332) + T2b birth scripts (B333) +
# T3 handoffs wiring (B334) + T4 three skills (B337+B338+B339)
# + T5 umbrella + T6 runbook (this).
#
# 10 commits across the arc; first complete domain rollout
# post-Phase α. Establishes the pattern for D3 / D8 / D1 / D2
# / D7 / D9 / D10 / D5 / D6 rollouts — same shape: ADR doc →
# roles → birth scripts → handoffs wiring → skills → umbrella +
# runbook.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/birth-d4-advanced.command \
        docs/runbooks/d4-advanced-rollout.md \
        dev-tools/commit-bursts/commit-burst340-adr0077-t5-t6-arc-closed.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d4): ADR-0077 T5+T6 - umbrella birth + runbook (B340) — ARC CLOSED 6/6

Burst 340. Final D4 advanced rollout burst.

What ships:

  - dev-tools/birth-d4-advanced.command (NEW): operator-driven
    umbrella that runs the three individual birth scripts in
    the recommended order — test_author (cheapest, no apply
    gate) → release_gatekeeper (advisory-only) → migration_pilot
    (most cautious, birth last). Aborts cleanly on any failure
    with clear progress reporting.

  - docs/runbooks/d4-advanced-rollout.md (NEW): end-to-end
    operator runbook. Setup, dispatch flow with worked examples
    for all three skills, observation, recovery for 6 known
    failure modes, explicit out-of-scope list.

T5 SBOM ownership decision: SBOM stays in dev-tools/
generate-sbom.command (existing). release_gatekeeper does NOT
own SBOM generation — would scope-creep T4c. Drift sentinel
already invoked by release_check.v1 can be extended to verify
SBOM presence later if operator hits concrete pain.

=== ADR-0077 CLOSED 6/6 ===
T1 (B331) + T2 (B332) + T2b (B333) + T3 (B334) + T4a (B337) +
T4b+T4c (B338+B339) + T5+T6 (this). 10 commits across the
arc. First complete domain rollout post-Phase α; establishes
the pattern for the remaining 9 domain rollouts."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 340 complete - ADR-0077 CLOSED 6/6 ==="
echo "First complete domain rollout post-Phase α."
echo ""
echo "Press any key to close."
read -n 1
