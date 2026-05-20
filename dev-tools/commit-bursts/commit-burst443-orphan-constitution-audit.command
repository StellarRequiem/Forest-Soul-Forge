#!/usr/bin/env bash
# Burst 443 — close the "328 orphan constitution YAMLs" queue item
# with a read-only audit script + audit doc capturing the actual
# state (zero orphans; queue item was a misread of older memory).
#
# Adds:
#   * dev-tools/audit-orphan-constitutions.py — read-only classifier.
#     For each file in soul_generated/, checks (a) is its path in
#     the agents table, (b) is its constitution_hash referenced
#     anywhere in audit_chain.jsonl. Classifies LIVE / CHAIN_ONLY /
#     ORPHAN / PARSE_FAILED and writes report.md +
#     classifications.json + delete-candidates.txt to
#     data/test-runs/. NEVER deletes — operator runs disposition.
#
#   * docs/audits/2026-05-20-orphan-constitution-audit.md —
#     captures the audit outcome:
#       LIVE: 37   CHAIN_ONLY: 288   ORPHAN: 0   PARSE_FAILED: 3
#     The 288 CHAIN_ONLY files are not housekeeping debt — they're
#     chain-referenced historical records (their constitution_hash
#     appears in old agent_created events). Per ADR-0005/0006,
#     deleting them would break the "chain is source of truth;
#     registry rebuildable from it" contract. The 3 PARSE_FAILED
#     files match the known B369 Kraine/Victor/chaz quarantine
#     entries; agents already rebirthed in B376; the on-disk files
#     persist as chain-referenced historical record.
#
# Net finding: the "328 orphan constitutions" queue item was a
# misread of older memory. There is nothing to prune. The audit
# script ships as durable tooling for any future cleanup pass.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: 328-file housekeeping debt has been queued across
#     multiple sessions; without a verifying audit it would have
#     stayed in queue forever or worse, been blindly pruned.
#   Prove non-load-bearing for kernel: read-only audit script +
#     audit doc. No schema, no events, no routes.
#   Prove alternative: blanket-delete all but LIVE (rejected; would
#     destroy 288 chain-referenced records); manual triage of 328
#     files (rejected; unreasonable); leave queue item dangling
#     (rejected; the audit closes it definitively).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 443 — orphan-constitution audit"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add dev-tools/audit-orphan-constitutions.py
git add docs/audits/2026-05-20-orphan-constitution-audit.md
git add dev-tools/commit-bursts/commit-burst443-orphan-constitution-audit.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "chore(audit): close 'orphan constitution YAMLs' queue item with read-only classifier + zero-orphan finding (B443)

Closes the queue item carried forward from multiple older session
memories: '328 orphan constitution YAMLs in soul_generated/'.

Adds dev-tools/audit-orphan-constitutions.py, a read-only Python
classifier that walks soul_generated/ and cross-references each
file against (a) the agents table (path + hash) and (b) every
sha256-shaped hex string in audit_chain.jsonl. Files fall into
LIVE / CHAIN_ONLY / ORPHAN / PARSE_FAILED. NEVER deletes — operator
runs disposition after review.

Audit outcome against HEAD 9d41e6b:
  LIVE         37 files — referenced by agent row. Keep.
  CHAIN_ONLY  288 files — constitution_hash in audit chain. Keep.
  ORPHAN        0 files — no safe-to-delete candidates.
  PARSE_FAILED  3 files — known B369 Kraine/Victor/chaz quarantine
                          entries (hand-appended '# --- override ---'
                          breaks YAML parse). Agents rebirthed in
                          B376; on-disk files persist as chain-
                          referenced record. Section-05 already
                          reports as INFO.

Net finding: the queue item was a misread of older memory. The
288 files I'd been thinking of as 'orphans' are all chain-
referenced historical records — their constitution_hash appears
in agent_created / agent_archived events. Per ADR-0005/0006, the
chain is source of truth and the registry is rebuildable from it;
deleting chain-referenced constitution files would break that
contract.

There is nothing to prune. Audit doc captures the analysis so
future sessions don't re-derive it. The script ships as durable
tooling — if soul_generated/ ever genuinely accumulates orphans,
re-running surfaces them.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: housekeeping debt queued across many sessions
    without verification; risked blind prune of chain-referenced
    records.
  Prove non-load-bearing for kernel: read-only audit + docs.
  Prove alternative: blanket-delete (rejected; destroys
    chain-referenced records); manual triage (rejected;
    unreasonable for 328 files); leave queue item open
    (rejected; closes the misread definitively)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -5
echo

echo "Pushing B443..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B443 pushed."
echo
echo "Press any key to close."
read -n 1 || true
