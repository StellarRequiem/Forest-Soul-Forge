#!/usr/bin/env bash
# Burst 66: memory_flag_contradiction.v1 — ADR-0036 T2.
#
# Closes the 'minimum bar: Verifier exists, can flag manually'
# milestone with Burst 65's T1 (Verifier role + Guardian-genre claim
# + constitutional template). The auto-scan loop (T3-T5) and lifecycle
# columns (T6-T7) are subsequent v0.3 bursts.
#
# Test delta: 1974 -> 1998 passing (+24).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 66 — memory_flag_contradiction.v1 (ADR-0036 T2) ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/memory_flag_contradiction.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        src/forest_soul_forge/core/memory.py \
        config/tool_catalog.yaml \
        tests/unit/test_memory_flag_contradiction_tool.py \
        commit-burst66.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ADR-0036 T2: memory_flag_contradiction.v1 tool

Action surface for the Verifier Loop. Stamps a row in
memory_contradictions naming both sides (earlier + later) of a
contradiction. Together with Burst 65 T1 (verifier_loop role) this
closes the 'minimum bar: Verifier exists, can flag manually'
milestone from ADR-0036.

The auto-scan loop (T3-T5) — candidate-pair pre-filter, LLM
classification, scheduler, /verifier/scan endpoint — and the
lifecycle columns (T6 schema v12 flagged_state column, T7 recall
extension) are subsequent v0.3 bursts.

Tool design:
- side_effects=filesystem (inserts into memory_contradictions).
- required_initiative_level=L3 — same posture as
  memory_challenge / memory_verify. Reactive Companion (L1)
  cannot autonomously flag; Verifier (Guardian L3) reaches.
- archetype_tags=[guardian]. Narrow reach; Verifier role and
  operator-driven kits only.

Distinct from memory_challenge.v1:
  challenge = ONE entry stamped as in-question (last_challenged_at).
  contradiction = TWO entries named (earlier + later) with a
                  contradiction_kind from the §7.3 enum.

Args (validated upfront before SQLite FK fires):
- earlier_entry_id (str, required)
- later_entry_id (str, required); must differ from earlier
- contradiction_kind: enum {direct, updated, qualified, retracted}
  per ADR-0027-am §7.3 CHECK
- confidence: enum {low, medium, high}; verifier_loop role refuses
  below high via constitutional gate (forbid_low_confidence_flag)
- note (str, optional); max 500 chars; lands in audit payload only

Output:
- contradiction_id (PK, contra_<hex>)
- earlier_entry_id / later_entry_id / contradiction_kind
- detected_at (ISO timestamp)
- detected_by = ctx.instance_id  (ADR-0036 §4.2 — defense-in-depth
  even though the constitutional require_detected_by_attribution
  policy enforces it)

Visibility gate (mirrors memory_verify / memory_challenge): each
entry must be reachable by the calling agent. A private entry
owned by another agent is unreachable; the tool refuses rather
than allowing a leaky permission grant.

The note (if any) lands in the audit-event metadata only — NOT on
the contradiction row. Same design as memory_challenge.v1: the
operator-narrated rationale is captured in the chain, the row
itself stays minimal.

memory.py adds the writer primitive:
  flag_contradiction(*, earlier_entry_id, later_entry_id,
                     contradiction_kind, detected_by) -> (id, ts)

The reader unresolved_contradictions_for() was already present from
ADR-0027-am T3. The reader/writer pair is now together in the
contradictions section.

Tests (test_memory_flag_contradiction_tool.py +24 cases):
- TestValidate (10): missing earlier/later, same-id-both-sides,
  invalid contradiction_kind enum, invalid confidence enum, note
  too long, note non-string, valid minimal, valid full,
  VALID_KINDS / VALID_CONFIDENCES constants pinned.
- TestExecute (8): row writes; visible via unresolved_lookup;
  audit_event_type=memory_contradiction_flagged metadata; note
  lands in metadata only (not on row); missing earlier refuses;
  missing later refuses; missing memory refuses;
  cross-private-scope visibility refusal; detected_by =
  ctx.instance_id (defense-in-depth).
- TestMemorySubsystem (2): flag_contradiction returns (id, ts);
  multiple flags on same pair produce distinct rows.
- TestRecallSurface (1): flagged contradictions surface via
  unresolved_contradictions_for (ADR-0027-am T3 integration).
- TestRegistration (3): tool registered with filesystem +
  required_initiative_level L3; catalog entry has both pinned;
  contradiction_kind enum matches ADR-0027-am §7.3 CHECK.

Test delta: 1974 -> 1998 passing (+24). Zero regressions.

Builtin tool count: 51 -> 52.

Next: Burst 67 — ADR-0036 T3 (scan implementation: candidate-pair
pre-filter + LLM classification + audit emission). Likely sized
across 2 bursts since the pre-filter logic + LLM-prompt design
are independent concerns."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 66 landed. memory_flag_contradiction.v1 in production."
echo "ADR-0036 minimum bar closed: Verifier exists + can flag manually."
echo "Next: Burst 67 (ADR-0036 T3 — scan implementation)."
echo ""
read -rp "Press Enter to close..."
