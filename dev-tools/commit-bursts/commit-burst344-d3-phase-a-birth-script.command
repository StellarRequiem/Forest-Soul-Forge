#!/bin/bash
# Burst 344 - ADR-0078 Phase A T2b: birth-forensic-archivist.command.
#
# Idempotent birth script for ForensicArchivist-D3. Mirrors the
# four-phase shape from birth-test-author.command (kickstart →
# birth POST → constitution patch → posture set).
#
# Key choices baked into this burst (vs. just-copy-from-D4):
#
# 1. Posture: GREEN, not YELLOW. Per ADR-0078 Decision 5,
#    chain-of-custody verification is non-acting. The gate is the
#    operator's later USE of the artifact, not the archivist's
#    attestation. TestAuthor-D4 was YELLOW because every code_edit
#    needs review; the archivist has nothing to gate.
#
# 2. Artifact storage path: data/forensics/. The ADR's "open
#    questions" deferred the bundle-vs-separate choice to this
#    burst; rationale captured in the script header. Audit chain
#    entries (attestations) stay at examples/audit_chain.jsonl;
#    artifact bytes live under data/forensics/. Clean boundary.
#
# 3. Per-tool constraints:
#      code_read         → data/forensics/ + audit chain only
#      file_integrity    → data/forensics/ + audit chain only
#      audit_chain_verify → no constraints (daemon-routed)
#    Explicitly forbid src/ + config/ + registry.sqlite + .env +
#    secrets so the archivist can't accidentally hash sensitive
#    runtime state.
#
# 4. EOF-tolerant `read -n 1 || true` per B341 — umbrella will
#    invoke this with stdin redirected to /dev/null.
#
# What ships:
#   dev-tools/birth-forensic-archivist.command (NEW, +x)

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/birth-forensic-archivist.command \
        dev-tools/commit-bursts/commit-burst344-d3-phase-a-birth-script.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d3): Phase A T2b - birth-forensic-archivist.command (B344)

Burst 344. Idempotent birth script for ForensicArchivist-D3.
Mirrors birth-test-author.commands four-phase shape (kickstart
to birth POST to constitution patch to posture set).

Key choices baked into this burst:

1. Posture: GREEN, not YELLOW. Per ADR-0078 Decision 5,
   chain-of-custody verification is non-acting. The gate is the
   operators later USE of the artifact, not the archivists
   attestation. TestAuthor-D4 was YELLOW because every code_edit
   needs review; the archivist has nothing to gate.

2. Artifact storage path: data/forensics/. The ADRs open
   questions deferred the bundle-vs-separate choice to this
   burst. Rationale: audit chain entries (attestations) stay at
   examples/audit_chain.jsonl; artifact bytes live under
   data/forensics/. Bundling MB-scale evidence into the chain
   would inflate it and break lazy-summarization. Separate tree
   also lets operators mount data/forensics/ to encrypted
   external storage without touching the audit chains locality.

3. Per-tool constraints:
     code_read          to data/forensics/ + audit chain only
     file_integrity     to data/forensics/ + audit chain only
     audit_chain_verify no constraints (daemon-routed)
   Explicitly forbid src/, config/, data/registry.sqlite, .env,
   and ~/.fsf/secrets so the archivist cant accidentally hash
   sensitive runtime state.

4. EOF-tolerant trailing read per B341 (read -n 1 || true) so
   the umbrella can invoke this with stdin redirected to /dev/null.

Birth-time auto-mkdir for data/forensics/: the operator creates
the canonical root once at birth; subsequent custody transitions
write per-incident subtrees. The archivist itself doesnt create
directories (its kit is read_only).

Next: B345 handoffs.yaml wiring + integration tests."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 344 complete - D3 Phase A T2b shipped ==="
echo "Next: B345 handoffs.yaml wiring + integration tests."
echo ""
echo "Press any key to close."
read -n 1 || true
