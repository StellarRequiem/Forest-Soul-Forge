#!/usr/bin/env bash
# Burst 43 Tranche 3c: memory_challenge.v1 tool (ADR-0027-am T4).
#
# Operator-driven scrutiny stamp on a memory entry. Distinct from
# memory_contradictions (which has a competing later entry); a
# challenge is "this is in question" without committing to what
# replaces it. Surfaces through memory_recall.v1's staleness flag.
#
# Test delta: 1521 -> 1538 passing (+17, 0 regressions).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 43 Tranche 3c — memory_challenge.v1 tool ==="
echo
clean_locks
git add src/forest_soul_forge/core/memory.py \
        src/forest_soul_forge/tools/builtin/memory_challenge.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_memory_challenge_tool.py \
        commit-burst43-tranche3c.command
clean_locks
git status --short
echo
clean_locks
git commit -m "memory_challenge.v1 tool (ADR-0027-am T4)

ADR-0027-amendment §7.4 + open question 4. New tool for operator-
driven scrutiny of a memory entry. Distinct from
memory_contradictions (which has a competing later entry):
a challenge is 'this is in question' without committing to a
replacement. Surfaces through memory_recall.v1's staleness flag —
challenged entries get a fresh last_challenged_at and reset their
staleness clock.

New: src/forest_soul_forge/tools/builtin/memory_challenge.py
- MemoryChallengeTool — name='memory_challenge', version='1',
  side_effects='filesystem'.
- Args: entry_id (required), challenger_id (required, operator
  handle / key fingerprint), note (optional, max 500 chars).
- Visibility gate mirrors memory_verify.v1: refuses challenges on
  private entries owned by another agent.
- Operator-only enforced via constitutional kit gating (mirrors
  memory_verify.v1's challenger_id pattern), not via runtime
  metadata. ADR-0027-am open question 4 acknowledged: the v0.2
  surface is operator-driven by convention; agent-self-challenge
  ambiguity (legitimate uncertainty vs. trust-signal manipulation)
  is deferred until a concrete agent-driven use case surfaces.
- Audit-event type 'memory_challenged' set on metadata so the
  runtime emits the right chain entry. Note (if any) lands in
  audit payload, not on the row.

Memory class addition (core/memory.py):
- mark_challenged(entry_id) -> str — stamps last_challenged_at to
  _now_iso() and returns the timestamp written. Idempotent in
  shape (always overwrites with NOW). Each call is a distinct
  audit-chain event when the caller emits 'memory_challenged'.

Catalog (config/tool_catalog.yaml):
- New memory_challenge.v1 entry. Required args entry_id +
  challenger_id; optional note (maxLength 500). side_effects
  filesystem. archetype_tags [observer, guardian] — same as
  memory_verify (the genres operators most commonly equip with
  scrutiny tools).

Registration (tools/builtin/__init__.py):
- MemoryChallengeTool imported, exported in __all__, registered
  via register_builtins.

Tests (tests/unit/test_memory_challenge_tool.py +17 cases):
- TestValidate (8): missing/empty/non-string entry_id +
  challenger_id rejected; note > 500 chars rejected; note at
  500-char boundary accepted; note omitted accepted.
- TestExecute (7): challenge stamps last_challenged_at; output
  shape; audit-event metadata; note in metadata + summary;
  missing entry raises; missing memory raises; private entry
  owned by other agent refused; idempotent rechallenge advances
  timestamp.
- TestRegistration (1): tool registered via register_builtins,
  catalog/registry consistency holds.
- TestStalenessSurface (1): end-to-end challenge → recall with
  staleness threshold flips is_stale from True to False.

Test delta: 1521 -> 1538 passing (+17). Zero regressions.

ADR-0027-am T4 status: implemented. Per-tool catalog entry +
registration land alongside the implementation so the
catalog/registry consistency invariant (test_b3_privileged_tools
TestRegistration) holds across the change."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Tranche 3c landed. ADR-0027-am T1+T2+T3+T4 complete."
echo "Remaining v0.2 work: ADR-0021-am T2/T3 (constitution + dispatcher)"
echo "                     ADR-0038 T3 (constitution honesty block)"
echo ""
read -rp "Press Enter to close..."
