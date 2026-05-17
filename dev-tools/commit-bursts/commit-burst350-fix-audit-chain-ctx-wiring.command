#!/bin/bash
# Burst 350 - fix audit_chain ctx wiring in dispatcher.
#
# SUBSTRATE BUG surfaced by D3 Phase A live verification.
# audit_chain_verify.v1's _resolve_chain only looked for the chain
# at ctx.constraints['audit_chain'], populated ONLY by the
# test_b1_tools.py fixture. The dispatcher never populated it.
# Result: the tool has been effectively dead code on the HTTP path
# since it shipped. The first real consumer (archive_evidence.v1's
# verify_chain_integrity step in D3 Phase A) blew up with
# ToolValidationError on first dispatch.
#
# Found in flight: live-test-d3-phase-a.command's run.log showed
# `dispatch status: failed`, `failure_reason: tool_failed`,
# `failed_step_id: verify_chain_integrity`, with the tool's own
# error message naming the fix: "The daemon must populate the
# chain ref before dispatching."
#
# Fix shape mirrors the pattern used by every other dispatcher-
# owned subsystem (memory, delegate, agent_registry,
# personal_index, procedural_shortcuts): a typed field on
# ToolContext, populated by the dispatcher's ToolContext(...)
# constructor call. Constraints-dict fallback retained so the
# existing test_b1_tools.py fixture stays green without rewrites.
#
# What ships:
#
# 1. src/forest_soul_forge/tools/base.py:
#    Add `audit_chain: Any = None` field to ToolContext after
#    personal_index. Inline comment explains the bug + B350 fix.
#
# 2. src/forest_soul_forge/tools/dispatcher.py:
#    Add `audit_chain=self.audit` to the ToolContext(...) call at
#    ~line 999. The dispatcher already holds self.audit (line ~461);
#    surfacing it on ctx is a one-line wire.
#
# 3. src/forest_soul_forge/tools/builtin/audit_chain_verify.py:
#    Reorder _resolve_chain to prefer ctx.audit_chain typed field
#    over constraints['audit_chain'] fallback. Order matters when
#    both are set: daemon-wired path is authoritative. Updated
#    docstring + raise message references B350.
#
# 4. tests/unit/test_audit_chain_verify_ctx_wiring.py (NEW):
#    7 assertions pinning the fix:
#      - ToolContext has audit_chain field (default None)
#      - typed field preferred over constraints fallback
#      - constraints fallback still works when typed is None
#      - typed-only happy path
#      - raise-when-neither-set with B350 in the error message
#      - constraints=None doesn't crash
#    Plus an existing-test regression check (test_b1_tools.py)
#    still passes 21/21.
#
# 5. live-test-d3-phase-a.command (UPDATED):
#    Bug fix from earlier this session — auth_header() echoed
#    "-H X-FSF-Token: $TOKEN" as a single string that bash word-
#    split into 3 args, leaving the header value empty + daemon
#    rejecting with "missing or invalid X-FSF-Token". Fix:
#    inline `-H "X-FSF-Token: $TOKEN"` on the two POST sites
#    (skills/reload + skills/run). This is what unblocked the
#    second live-test run that found the audit_chain bug.
#
# 6. dev-tools/verify-d3-phase-a-live.command (UPDATED):
#    Bug fix from earlier this session — the wrapper called
#    force-restart-daemon.command which `exec`s start.command
#    which runs uvicorn in the FOREGROUND. The wrapper's parent
#    shell blocked forever on the foreground tail. Fix: drop the
#    explicit restart step; the birth umbrella's launchctl
#    kickstart is sufficient for a normal restart cycle. Inline
#    comment captures the reason for future-us.
#
# 7. dev-tools/birth-d3-phase-a.command (NO CHANGE — bundled here
#    for the chmod metadata; was committed in B347 but its +x bit
#    may not have propagated if someone re-checked it out).
#    [Actually leaving birth scripts alone — no change required.]
#
# Test results: 28/28 green (7 new B350 + 21 regression).
#
# Discovery method: the D3 Phase A live verification chain
# (operator-driven by Alex per his "drive the runtime, watch
# what happens" preference). Without that runtime exercise, this
# bug would have shipped to D3 Phase B+'s telemetry_steward
# (which also calls audit_chain_verify), Phase D's purple_pete,
# any future skill needing chain integrity. Caught at the right
# layer in the right phase.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/base.py \
        src/forest_soul_forge/tools/dispatcher.py \
        src/forest_soul_forge/tools/builtin/audit_chain_verify.py \
        tests/unit/test_audit_chain_verify_ctx_wiring.py \
        live-test-d3-phase-a.command \
        dev-tools/verify-d3-phase-a-live.command \
        dev-tools/commit-bursts/commit-burst350-fix-audit-chain-ctx-wiring.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(dispatcher): wire audit_chain into ToolContext (B350)

Burst 350. SUBSTRATE BUG surfaced by D3 Phase A live verification.
audit_chain_verify.v1s _resolve_chain only looked for the chain at
ctx.constraints['audit_chain'], populated ONLY by the
test_b1_tools.py fixture. The dispatcher never populated it. The
tool has been effectively dead code on the HTTP path since it
shipped. First real consumer (archive_evidence.v1s
verify_chain_integrity step in D3 Phase A) blew up with
ToolValidationError on first dispatch.

Found via live-test-d3-phase-a.commands run.log:
  dispatch status: failed
  failure_reason: tool_failed
  failed_step_id: verify_chain_integrity
  failure_detail: tool audit_chain_verify.v1 raised ToolValidationError
The tools own error message named the fix: \"The daemon must
populate the chain ref before dispatching.\"

Fix mirrors the pattern used by every other dispatcher-owned
subsystem (memory, delegate, agent_registry, personal_index,
procedural_shortcuts): typed field on ToolContext, populated by
the dispatchers ToolContext(...) constructor call. Constraints-
dict fallback retained so the existing test_b1_tools.py fixture
stays green.

src/forest_soul_forge/tools/base.py:
  Add audit_chain: Any = None field to ToolContext.

src/forest_soul_forge/tools/dispatcher.py:
  Add audit_chain=self.audit to the ToolContext(...) call.

src/forest_soul_forge/tools/builtin/audit_chain_verify.py:
  _resolve_chain now prefers ctx.audit_chain typed field over
  constraints['audit_chain'] fallback. Order: daemon-wired path
  wins when both set. Error message references B350.

tests/unit/test_audit_chain_verify_ctx_wiring.py (NEW): 7
  assertions pinning the fix (typed field exists, default None,
  resolution order, fallback works, raise message references
  B350, constraints=None handled defensively).

Also folds in two earlier-this-session bug fixes around the live
verification:

live-test-d3-phase-a.command:
  Auth header bug. auth_header() echoed the X-FSF-Token header as
  a single string that bash word-split into 3 args, leaving the
  header value empty. Fix: inline the header on the two POST
  sites (skills/reload + skills/run).

dev-tools/verify-d3-phase-a-live.command:
  Wrapper hang. Called force-restart-daemon.command which
  execs start.command which runs uvicorn in the FOREGROUND. The
  wrappers parent shell blocked forever on the foreground tail.
  Fix: drop the explicit restart step (birth umbrellas launchctl
  kickstart is sufficient).

Tests: 28/28 green (7 new B350 + 21 regression test_b1_tools.py).

Without the live-verify pass, this bug would have shipped to
Phase Bs telemetry_steward, Phase Ds purple_pete, and any future
skill needing chain integrity. Caught at the right layer in the
right phase."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 350 complete - audit_chain ctx wiring fixed ==="
echo "Next: re-run live-test-d3-phase-a.command to confirm fix in"
echo "the wild + take Phase A live end-to-end."
echo ""
echo "Press any key to close."
read -n 1 || true
