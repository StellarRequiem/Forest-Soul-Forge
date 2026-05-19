#!/bin/bash
# Burst 417 - audit chain May 11 race documentation + section-08 set extension.
#
# Today's wiring_audit_triage runs were reporting chain_ok=False
# every cycle because audit_chain_verify hits the first fork at
# seq=3728 (already documented as a May 8 race in
# docs/audits/2026-05-17-audit-chain-seq-3728-fork.md).
#
# Full scan to plan a quarantine surfaced a SECOND race episode
# from 2026-05-11 (seqs 7695-7703, 9 duplicate-seq entries) that
# wasn't in KNOWN_HISTORICAL_FORKS. Same root cause as May 8
# (pre-B199 mutex fix); identical disposition.
#
# What this commit adds:
#
# 1. docs/audits/2026-05-19-audit-chain-may11-race.md (NEW)
#    Documents the May 11 episode + reaffirms the no-substrate-
#    change disposition. Cross-references the May 8 doc.
#
# 2. dev-tools/diagnostic/section-08-audit-chain-forensics.command
#    KNOWN_HISTORICAL_FORKS extended with the 9 new seqs. Comment
#    block names both episodes + audit-doc pointers. Set is now
#    {3728, 3735-3738, 3740, 7695-7703} (15 total).
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: triune triage reasons about chain_ok=False every
#     cycle. False-severity inflation.
#   Prove non-load-bearing: doc + section-08 set extension. Chain
#     file unchanged. New seqs are tolerated only by section-08;
#     audit_chain_verify still reports the underlying break (so a
#     post-B199 regression would still be visible).
#   Prove alternative: rewriting the chain to remove duplicate-seq
#     entries violates append-only; expanding the tolerance set is
#     the documented + audit-doc-traced pattern from May 8.
#
# Queued substrate enhancement: wiring_audit.v1 should consult
# KNOWN_HISTORICAL_FORKS so its chain_ok flag reflects "ok modulo
# known historical forks" rather than raw verify output. Removes
# the recurring noise from triune triage. Out of scope for B417
# (would need a shared module or audit chain config).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/audits/2026-05-19-audit-chain-may11-race.md \
        dev-tools/diagnostic/section-08-audit-chain-forensics.command \
        dev-tools/commit-bursts/commit-burst417-may11-chain-fork-doc.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(audit): May 11 chain race + section-08 set extension (B417)

Burst 417. Triune triage reported chain_ok=False every cycle
because verify stops at seq=3728 (May 8 race, already documented
in 2026-05-17-audit-chain-seq-3728-fork.md).

Full scan surfaced a SECOND race episode from 2026-05-11 (seqs
7695-7703, 9 duplicate-seq entries) not yet in KNOWN_HISTORICAL_
FORKS. Same root cause + disposition as May 8.

Adds:
  docs/audits/2026-05-19-audit-chain-may11-race.md  (NEW)
  section-08-audit-chain-forensics.command  KNOWN_HISTORICAL_FORKS
    extended: {3728, 3735-3738, 3740, 7695-7703} (15 total)

Disposition: identical to May 8 — no substrate change. Chain is
append-only. Section-08 INFO-classifies these specific seqs; any
NEW duplicate_seq outside the set continues to FAIL loudly (would
indicate a post-B199 mutex regression).

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: triune triage reasons about false chain_ok=False.
  Prove non-load-bearing: doc + set extension; chain unchanged.
  Prove alternative: rewriting chain violates append-only; tolerance
    set is the documented pattern from May 8.

Queued substrate enhancement: wiring_audit.v1 should consult
KNOWN_HISTORICAL_FORKS so its chain_ok reflects 'ok modulo known
historical forks'. Removes recurring noise from triune outputs.
Out of scope for B417 (needs shared module / audit chain config)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 417 complete - May 11 race documented ==="
echo "=========================================================="
echo "Press any key to close."
read -n 1 || true
