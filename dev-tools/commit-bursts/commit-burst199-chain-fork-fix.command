#!/bin/bash
# Burst 199 — audit-chain fork fix (3-layer defense).
#
# Context: the live audit chain at examples/audit_chain.jsonl has 6
# duplicate seqs (3728, 3735-3738, 3740) — write races between the
# scheduler and the plugin runtime, both bypassing app.state.write_lock.
# Project's own AuditChain.verify() returns ok=False; external
# scan_for_forks confirms 6 forks. Forensic record:
# docs/audits/2026-05-08-chain-fork-incident.md.
#
# What ships:
#
#   src/forest_soul_forge/core/audit_chain.py:
#     - import threading
#     - module docstring updated: "single-writer at the in-process
#       level: append() holds an internal RLock; that lock remains
#       the cross-resource serializer but the chain's own integrity
#       is no longer hostage to caller discipline"
#     - AuditChain.__init__: self._append_lock = threading.RLock()
#     - AuditChain.append: body wrapped in `with self._append_lock:`
#     - KNOWN_EVENT_TYPES: 14 new entries (ADR-0033/0034/0041/0045/
#       0048/0053/0056) for events that were emitted but not in the
#       allowlist; pre-B199 the verifier silently warned on every walk
#     - ForkScanResult dataclass added (sister of VerificationResult)
#     - AuditChain.scan_for_forks(): walks entire chain, reports
#       every duplicate seq + every hash mismatch without short-
#       circuiting; sister of verify()
#
#   src/forest_soul_forge/daemon/app.py:
#     - lifespan: build_plugin_runtime() now wrapped in `with
#       app.state.write_lock:` (was bypassed pre-B199)
#
#   src/forest_soul_forge/daemon/plugins_runtime.py:
#     - build_plugin_runtime: stale "no concurrent writers yet"
#       comment rewritten to explain the lifespan ordering bug and
#       point at the audit doc
#
#   src/forest_soul_forge/daemon/scheduler/runtime.py:
#     - _dispatch: pre-runner emit and post-runner emit+persist now
#       wrapped in app.state.write_lock. NOT held during await
#       runner() — that's the slow path and would block HTTP routes.
#       Two short critical sections, RLock allows nested acquisition.
#
#   tests/unit/test_audit_chain.py:
#     - TestConcurrentAppend: 3 new tests (16-thread storm, on-disk
#       seqs strictly increasing, RLock re-entrance). The first two
#       would have flaked pre-B199.
#     - 4 new scan_for_forks tests including the explicit
#       "doesn't short-circuit on multi-fork chain" coverage that
#       distinguishes scan_for_forks from verify.
#
#   dev-tools/check-chain-forks.sh: NEW.
#     Operator script. Wraps scan_for_forks. Exits 0 if clean,
#     1 if any anomaly. Suitable for CI / pre-tag gating.
#
#   docs/audits/2026-05-08-chain-fork-incident.md: NEW.
#     Full forensic record. §0 Hippocratic gate verdict, root cause
#     trace, 3-layer fix description, regression coverage, what we
#     deliberately did NOT do (the 6 historical forks stay on the
#     chain — append-only includes its broken parts).
#
# What we deliberately did NOT do:
#   - Erase or rewrite the 6 historical forks. The chain is append-
#     only by invariant — including the broken parts. Doing so would
#     be a worse violation than the original bug. They are now
#     forensic record.
#   - Add chain_breach_noted as an event type. That's a design
#     question for a future burst, not a bug fix.
#   - Change the AuditChain.verify() short-circuit behavior.
#     Short-circuit-on-first-break is correct for "is this chain
#     still trustworthy"; scan_for_forks is the right tool for
#     "where are all the breaches" (different question).
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — chain.append signature, the
# JSONL schema, and verify()'s return shape are all unchanged.
# Internal locking is invisible to callers.
#
# Verification:
#   - tests/unit/test_audit_chain.py: 39 passed (was 32, +7 new)
#   - full unit suite: 2,598 passed / 62 failed / 11 skipped / 1
#     xfailed. The 62 failures are pre-existing X-FSF-Token fixture
#     drift unrelated to B199. Pre-B199 baseline was 2,591 passed /
#     62 failed; the +7 are exactly the new tests this burst added.
#   - bash dev-tools/check-chain-forks.sh: reports 6 forks on the
#     live chain (the historical breach), exits 1. Going forward,
#     no new forks should ever appear.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/daemon/app.py \
        src/forest_soul_forge/daemon/plugins_runtime.py \
        src/forest_soul_forge/daemon/scheduler/runtime.py \
        tests/unit/test_audit_chain.py \
        dev-tools/check-chain-forks.sh \
        docs/audits/2026-05-08-chain-fork-incident.md \
        dev-tools/commit-bursts/commit-burst199-chain-fork-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(audit): audit-chain fork fix — 3-layer defense (B199)

Burst 199. Race condition surfaced in the live chain by Forest's own
AuditChain.verify() which returned ok=False at seq 3728. External
scan revealed 6 duplicate seqs in total (3728, 3735-3738, 3740) —
write races between the scheduler and the plugin runtime, both
bypassing app.state.write_lock. Forensic record:
docs/audits/2026-05-08-chain-fork-incident.md.

Layer 2 (defense in depth): AuditChain.__init__ now constructs an
internal threading.RLock. AuditChain.append wraps its read-of-head +
hash-compute + write + advance-of-head sequence in that lock. The
chain is now self-protecting against in-process concurrent writers
regardless of caller discipline. app.state.write_lock remains the
cross-resource serializer (chain + registry SQLite + plugin filesystem
advance together) — but the chain's own integrity is no longer
hostage to the discipline.

Layer 1 (surgical, belt to Layer 2 suspenders): two specific call
sites that were bypassing write_lock are fixed.

  - app.py lifespan: build_plugin_runtime() now wrapped in
    \`with app.state.write_lock:\`. Pre-B199 the comment claimed
    'lifespan owns the only handle and there are no concurrent
    writers yet' — false, because scheduler.start() runs ten lines
    earlier and the scheduler is already ticking.

  - scheduler/runtime.py::_dispatch: pre-runner emit + post-runner
    emit+persist sections now wrapped. The lock is explicitly NOT
    held during await runner(...) — that's the slow path (multi-
    second LLM call) and holding the lock through it would block
    HTTP routes. Two short critical sections, RLock allows nested
    acquisition.

Layer 3 (verifier hardening):

  - KNOWN_EVENT_TYPES: 57 -> 71. Added 14 events that were already
    emitted but not in the allowlist (ADR-0033/0034/0041/0045/0048/
    0053/0056). Pre-B199 the verifier silently logged them as
    forward-compat warnings on every walk.

  - New AuditChain.scan_for_forks() method + ForkScanResult
    dataclass. Walks the entire chain, reports every duplicate seq
    + hash mismatch without short-circuiting. verify() unchanged
    (its short-circuit is correct for 'is this chain still
    trustworthy'); scan_for_forks answers the different question
    'where are all the breaches'.

  - dev-tools/check-chain-forks.sh: operator script wrapping the
    scan. Exits 0 if clean, 1 if any anomaly.

Regression coverage in tests/unit/test_audit_chain.py:

  - TestConcurrentAppend: 16-thread × 50-append storm; on-disk seqs
    must be strictly increasing; lock must be re-entrant. The first
    two would have flaked pre-B199.
  - 4 scan_for_forks tests including explicit no-short-circuit
    coverage on a multi-fork chain.

What we deliberately did NOT do: the 6 historical forks at chain
seqs 3728/3735-3738/3740 stay on disk. The chain is append-only by
invariant — including its broken parts. Erasing them would be a
worse violation than the original bug.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes. Chain.append signature, JSONL
schema, verify() return shape all unchanged. Internal locking is
invisible to callers.

Verification: full unit suite 2598 passed / 62 failed / 11 skipped /
1 xfailed. The +7 vs pre-B199 baseline are exactly the new tests
added here. The 62 failures are pre-existing X-FSF-Token fixture
drift, unrelated."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 199 complete ==="
echo "=== Chain integrity restored; historical breach documented. ==="
echo "Press any key to close."
read -n 1
