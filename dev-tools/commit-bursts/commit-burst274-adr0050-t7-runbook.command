#!/bin/bash
# Burst 274 — ADR-0050 T7: encryption-at-rest operator runbook.
#
# Seventh tranche of the encryption-at-rest arc. Docs-only.
#
# What ships:
#
# docs/runbooks/encryption-at-rest.md — operator-facing runbook
# covering:
#
#   1. What encryption-at-rest is + the integrity vs.
#      confidentiality framing (ADR-0049 sibling)
#   2. What gets encrypted (4 surfaces) and what stays plaintext
#      by design (structural fields the verifier needs)
#   3. Quick-start enablement under each of the three backends
#      (Keychain, file, passphrase) with exact env-var commands
#   4. How to verify encryption is actually on (startup_diagnostics
#      + data-layer sanity checks)
#   5. Backing up the master key per backend + the explicit
#      "this is unrecoverable if lost" disclosure
#   6. Key-loss recovery procedure
#   7. Mixed deployment workflow (some agents pre-T5 plaintext,
#      some encrypted) + the plaintext→encrypted migration gap
#      (queued for T8)
#   8. Backend-selection details + when to pick each
#   9. The Argon2id → Scrypt amendment note (transparency about
#      the substrate decision that diverged from the ADR text)
#  10. Performance overhead notes per surface
#  11. Common failure modes + what to do for each
#  12. Threat model — what this covers and explicitly what it
#      doesn't (trusted-host boundary)
#
# Cross-references the sibling runbooks: per-event-signatures.md
# (ADR-0049 integrity half) and tool-sandbox.md (ADR-0051 Phase-4
# security-hardening sibling).
#
# No code changes. No test changes. Purely operator-facing
# documentation.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/runbooks/encryption-at-rest.md \
        dev-tools/commit-bursts/commit-burst274-adr0050-t7-runbook.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: ADR-0050 T7 — encryption-at-rest operator runbook (B274)

Burst 274. Seventh tranche of the encryption-at-rest arc. Docs only.

Adds docs/runbooks/encryption-at-rest.md covering:

  - Quick start under each backend (Keychain / file / passphrase)
    with exact env-var commands
  - Verification — startup_diagnostics grep + data-layer sanity
    checks (sqlite3 against the encrypted registry; jq against
    audit chain envelopes)
  - Master-key backup per backend + the explicit \"unrecoverable
    if lost\" disclosure
  - Key-loss recovery procedure
  - Mixed-deployment workflow (encrypted + plaintext agents
    coexisting per ADR Decision 6) + the plaintext→encrypted
    migration gap that T8 closes
  - Argon2id→Scrypt amendment note (substrate diverged from
    ADR Decision 5 text; rationale + future migration path)
  - Per-surface performance overhead notes
  - Common failure modes + what to do for each
  - Threat-model boundary (what trusted-host covers, what it
    doesn't)

Cross-references per-event-signatures.md (integrity sibling) +
tool-sandbox.md (Phase-4 security-hardening sibling).

Last code-touch for ADR-0050 is T8 (B275) — the fsf encrypt CLI
that adds key rotation, status surfacing, and the in-place
plaintext→encrypted registry migration that the runbook references
as queued."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 274 complete — ADR-0050 T7 runbook shipped ==="
echo "Remaining: T8 (fsf encrypt CLI — rotate-key / status / migrate)."
echo ""
echo "Press any key to close."
read -n 1
