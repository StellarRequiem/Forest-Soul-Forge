#!/bin/bash
# Burst 364 - audit chain seq-3728 fork disposition + section-08
# historical-fork awareness.
#
# Bug shape (surfaced by diagnostic-all on 2026-05-17):
#   section-08-audit-chain-forensics FAIL:
#     "audit_chain_verify end-to-end - broken_at_seq=3728,
#      reason=seq gap: expected 3729, got 3728"
#
# What the read of examples/audit_chain.jsonl actually shows:
#   - Two entries share seq=3728: a plugin_installed at file line
#     3729 (entry_hash a466...) and a scheduled_task_dispatched at
#     line 3730 (entry_hash dd8e...). Both claim prev_hash=572c...
#     (seq 3727's hash). The scheduled_task fork wins - seq 3729
#     onward references dd8e... as its prev_hash. The
#     plugin_installed at line 3729 is orphaned.
#
# Why this is already known:
#   src/forest_soul_forge/core/audit_chain.py:497-507 documents
#   that 'pre-B199 forks at chain seqs 3728 / 3735-3738 / 3740 are
#   the canonical example' of write-race duplicate_seqs. ADR-0050
#   B199 introduced the per-chain mutex that prevents the race in
#   the writer; entries from before that fix are immutable
#   historical record (the chain is append-only).
#
# Disposition (audit doc): docs/audits/2026-05-17-audit-chain-seq-
#   3728-fork.md captures the investigation, the read of the file,
#   the cross-reference to audit_chain.py's documented set, and
#   the rationale for probe-side-only fix.
#
# Fix shape (probe-side only - NO substrate change):
#   section-08-audit-chain-forensics.command:
#     KNOWN_HISTORICAL_FORKS = {3728, 3735, 3736, 3737, 3738, 3740}.
#     When audit_chain_verify reports broken_at_seq in this set,
#     the section emits INFO (with a pointer to ADR-0050) instead
#     of FAIL. Any NEW broken_at_seq outside the set continues to
#     FAIL loudly - regressions on the B199 mutex fix surface
#     immediately.
#
# Why NO substrate change:
#   1. Truncating/rewriting examples/audit_chain.jsonl would
#      itself be exactly the kind of mutation the chain is built
#      to detect. The file is its own audit-evidence artifact.
#   2. Emitting a chain_repair_event would imply 'this is being
#      fixed now' - but the writer race is already fixed (B199).
#      The forks are historical; nothing forward to repair.
#   3. CLAUDE.md sec0 step 3 ('prove alternative is strictly
#      better than leaving in place') passes only for the probe-
#      side fix; substrate changes here are worse.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: section-08 FAIL daily on documented historical
#     state, polluting drift detection.
#   Prove non-load-bearing: chain semantic untouched; the
#     verifier still reports every break, just classifies known
#     historical ones as INFO.
#   Prove alternative is strictly better: alternatives violate
#     append-only or pretend to fix what's already fixed. Probe-
#     side classification preserves both visibility AND signal-
#     to-noise.
#
# Verification after this commit lands:
#   1. Re-run section-08-audit-chain-forensics.command - seq-3728
#      FAIL flips to INFO with ADR-0050 pointer; section-08 drops
#      one FAIL.
#   2. If a NEW writer race surfaces a duplicate_seq outside the
#      known set (regression on B199), section 08 still catches
#      it as FAIL on first run. The mutex regression check in
#      docs/audits/2026-05-17-audit-chain-seq-3728-fork.md covers
#      the investigation path.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-08-audit-chain-forensics.command \
        docs/audits/2026-05-17-audit-chain-seq-3728-fork.md \
        dev-tools/commit-bursts/commit-burst364-audit-chain-historical-fork-awareness.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(harness): audit chain historical-fork awareness (B364)

Burst 364. Closes the section-08 seq=3728 FAIL with probe-side
classification.

Investigation (docs/audits/2026-05-17-audit-chain-seq-3728-fork.md):
  Two entries share seq=3728 at file lines 3729 and 3730. Both
  claim prev_hash=572c... (seq 3727). The scheduled_task fork
  wins; seq 3729 onward references dd8e... as prev_hash. The
  plugin_installed at line 3729 is orphaned.

This is a documented pre-B199 write-race artifact. The audit
chain code's ForkScanResult docstring (core/audit_chain.py:497-
507) names 'seqs 3728 / 3735-3738 / 3740' as the canonical
example. ADR-0050 B199 introduced the per-chain mutex that
prevents the race; entries from before that fix are immutable
historical record (chain is append-only).

Disposition: probe-side only, NO substrate change.
  section-08-audit-chain-forensics.command adds
  KNOWN_HISTORICAL_FORKS = {3728, 3735, 3736, 3737, 3738, 3740}.
  When audit_chain_verify reports broken_at_seq in this set,
  emit INFO with ADR-0050 pointer instead of FAIL. NEW broken
  seqs still FAIL - regressions on the B199 mutex fix surface
  immediately.

Why NO substrate change:
  1. Truncating/rewriting the chain is exactly what append-only
     forbids; the file IS the audit-evidence artifact.
  2. chain_repair_event implies 'fixing now' - but the writer
     race is already fixed (B199). Nothing forward to repair.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: section-08 FAIL daily on documented state.
  Prove non-load-bearing: chain semantic untouched; verifier
    still reports every break, just classifies known ones.
  Prove alternative is better: alternatives violate append-only
    or pretend to fix what's already fixed.

After this lands: section-08 drops the seq-3728 FAIL to INFO."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 364 complete - audit chain fork awareness ==="
echo "=========================================================="
echo "Re-test: dev-tools/diagnostic/section-08-audit-chain-forensics.command"
echo "Audit doc: docs/audits/2026-05-17-audit-chain-seq-3728-fork.md"
echo ""
echo "Press any key to close."
read -n 1 || true
