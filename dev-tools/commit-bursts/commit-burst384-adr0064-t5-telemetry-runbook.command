#!/bin/bash
# Burst 384 - ADR-0064 T5: telemetry pipeline operator runbook.
#
# Doc-only commit. Captures the operator-facing surface for the
# T1-T4 substrate that's already live.
#
# What lands:
#
#   docs/runbooks/telemetry-pipeline.md (NEW)
#     Sections:
#       - Pipeline shape (visual flow: adapter -> ingestor -> store
#         -> chain anchor)
#       - Why store-first / anchor-second
#       - Daily operator workflow (glance / spot-verify / investigate)
#       - Backups (cp pattern; chain + store should be paired)
#       - Retention sweep (manual run today; default policy table
#         maps class -> TTL)
#       - Adapter management (how to add / pause / debug)
#       - Recovery flows for each verify-CLI verdict:
#           MISMATCH (real tampering)
#           CHAIN_ENTRY_MISSING (mid-flush crash window)
#           BATCH_EMPTY (typo or retention sweep)
#           STORE_UNAVAILABLE (operational)
#       - Cross-references to ADR-0064 + all bursts T1-T4 + the
#         queued T6 close
#       - Verification checklist
#
# Why a runbook before T6 closes the ADR:
#   T1-T4 substrate is live and the operator is using it (T4
#   landed B379). T6's micro-batching adds throughput-tuning + the
#   threat_intel_curator role; it doesn't change the existing
#   operator workflows. Shipping the runbook now means the
#   operator has documented surface before the substrate gets
#   more layers; T6's runbook delta is smaller for landing later.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T5: operator has working
#     substrate but no documented recovery flow for the four
#     verify-CLI verdicts. A MISMATCH (real tampering) surfaces
#     without a documented response path; same for the mid-flush
#     CHAIN_ENTRY_MISSING window.
#   Prove non-load-bearing: doc only. No substrate or frontend
#     change.
#   Prove alternative is strictly better: leaving as tribal
#     knowledge loses fidelity across sessions; chat-only is
#     ephemeral.
#
# What this UNBLOCKS / CLOSES:
#   ADR-0064 T5 shipped. T6 is the closing burst (micro-batching
#   + threat_intel_curator role + D3 Phase B close).
#
# Verification after this commit lands:
#   1. Read docs/runbooks/telemetry-pipeline.md end-to-end.
#   2. Substrate unchanged; no daemon restart needed.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/telemetry-pipeline.md \
        dev-tools/commit-bursts/commit-burst384-adr0064-t5-telemetry-runbook.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(runbooks): ADR-0064 T5 telemetry pipeline runbook (B384)

Burst 384. Operator runbook for the live telemetry substrate
(T1-T4). T6 closes ADR-0064 + D3 Phase B as the next burst.

docs/runbooks/telemetry-pipeline.md (NEW):
  Sections:
    - Pipeline shape diagram (adapter -> ingestor -> store ->
      chain anchor) + store-first/anchor-second rationale.
    - Daily operator workflow (glance / spot-verify / investigate).
    - Backups (pair store + chain; restore both or prefer chain).
    - Retention sweep (manual run today; class -> TTL table).
    - Adapter management (add / pause / debug).
    - Recovery flows per verify-CLI verdict:
        MISMATCH (tampering) / CHAIN_ENTRY_MISSING (mid-flush
        crash) / BATCH_EMPTY (typo or swept) / STORE_UNAVAILABLE.
    - Cross-references + verification checklist.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: substrate live but no documented response for
    the four verify-CLI verdicts.
  Prove non-load-bearing: doc only.
  Prove alternative is better: tribal-knowledge loses fidelity.

After this lands: ADR-0064 T5 shipped. T6 (micro-batching +
threat_intel_curator) closes Phase B."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 384 complete - telemetry runbook ==="
echo "=========================================================="
echo "Runbook: docs/runbooks/telemetry-pipeline.md"
echo "Next: B385 (ADR-0064 T6 - micro-batching + threat_intel_curator)"
echo ""
echo "Press any key to close."
read -n 1 || true
