#!/bin/bash
# Burst 324 - ADR-0076 T6: PersonalIndex operator runbook.
#
# Closes ADR-0076. Operator-facing runbook covering enabling the
# substrate, observing index health, rebuilding from SQL truth,
# swapping embedders, and recovering from common failure modes.
#
# What ships:
#
# 1. docs/runbooks/personal-index.md (NEW):
#    Sections:
#      - At a glance (index purpose + the two data flows in)
#      - Enabling the substrate (env var + restart, /healthz
#        diagnostic to confirm)
#      - Reading via personal_recall.v1 (mode choice, output
#        shape, audit-chain privacy invariant)
#      - Observing index health (indexer.status() snapshot,
#        symptom→action table)
#      - Rebuilding the index (when + procedure, fsf index
#        rebuild walk-through, encrypted-row caveat)
#      - Swapping embedders (always rebuild after swap, model
#        pre-pull for offline deploys)
#      - Recovery — common failure modes:
#        * "personal index not wired"
#        * indexer worker died
#        * recall returns wrong/stale rows
#        * genre rejection
#      - Reference (links to T1-T5 ADR sections)
#
# === ADR-0076 CLOSED 6/6 ===
# Vector index for personal context arc complete. Phase α
# scorecard: 7/10 closed (0050, 0067, 0068, 0071, 0073, 0074,
# 0075, 0076). Only ADR-0070 voice tail + ADR-0072 provenance
# T3 still partial.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/personal-index.md \
        dev-tools/commit-bursts/commit-burst324-adr0076-t6-runbook.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(memory): ADR-0076 T6 - PersonalIndex runbook (B324) — ARC CLOSED 6/6

Burst 324. Closes ADR-0076. Operator-facing runbook covering
enabling the substrate, observing index health, rebuilding from
SQL truth, swapping embedders, and recovering from common
failure modes.

What ships:

  - docs/runbooks/personal-index.md: sections cover At a Glance
    (purpose + two write flows), Enabling (env var +
    /healthz diagnostic), Reading via personal_recall.v1 (mode
    choice, privacy invariant), Observing Health (status()
    snapshot + symptom→action table), Rebuilding (when +
    fsf index rebuild walkthrough + encrypted-row caveat),
    Swapping Embedders (always rebuild + offline model
    pre-pull), Recovery (4 failure modes), Reference (T1-T5
    cross-links).

=== ADR-0076 CLOSED 6/6 ===
Vector index for personal context arc complete. Phase α
scorecard: 7/10 closed (0050, 0067, 0068, 0071, 0073, 0074,
0075, 0076). Only ADR-0070 voice tail + ADR-0072 provenance
T3 still partial."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 324 complete - ADR-0076 CLOSED 6/6 ==="
echo "Phase alpha: 7/10 scale ADRs closed."
echo ""
echo "Press any key to close."
read -n 1
