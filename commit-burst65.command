#!/usr/bin/env bash
# Burst 65: ADR-0036 T1 — verifier_loop role + Guardian-genre claim
# + constitutional template.
#
# First v0.3 ADR-0036 tranche. Verifier exists as a birthable role;
# can be born via the standard /birth flow. T2 (the
# memory_flag_contradiction.v1 tool) and T3-T7 (scan loop, scheduler,
# dashboard surface) follow in subsequent bursts.
#
# Test delta: 1968 -> 1974 passing (+6).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 65 — ADR-0036 T1 verifier_loop role ==="
echo
clean_locks
git add config/trait_tree.yaml \
        config/genres.yaml \
        config/constitution_templates.yaml \
        tests/unit/test_trait_engine.py \
        tests/unit/test_constitution.py \
        commit-burst65.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ADR-0036 T1: verifier_loop role + Guardian-genre claim

First tranche of ADR-0036 (Verifier Loop — auto-detected memory
contradictions). Adds a birthable verifier_loop role with
Guardian-genre claim, constitutional template policies, and
end-to-end birth verification. T2 (memory_flag_contradiction.v1
tool) and T3-T7 (scan loop, scheduler, dashboard surface) follow
in subsequent bursts.

trait_tree.yaml additions:
- verifier_loop role with audit-peak (2.5) domain weights:
  security 1.5, audit 2.5, cognitive 1.5, communication 0.7,
  emotional 0.4, embodiment 0.4. Audit peaks at 2.5 because the
  Verifier's whole job is auditing memory; modest cognitive
  reflects LLM classification at moderate depth, not deep
  reasoning; low emotional + embodiment because it operates
  through the audit chain + memory tools, not user-facing surfaces.

genres.yaml additions:
- guardian.roles list extended with verifier_loop. Guardian's
  read_only ceiling + L3/L3 initiative posture inherited.

constitution_templates.yaml additions (verifier_loop role_base):
- forbid_direct_memory_write — Verifier never writes to another
  agent's memory directly; all flagging goes through
  memory_flag_contradiction.v1 stamping the contradictions table.
- forbid_low_confidence_flag — ADR-0036 §4.1 false-positive
  mitigation. Flag only at LLM confidence >= 0.80. Low-confidence
  cases are skipped, not auto-flagged. False contradictions
  trigger H-6 (memory overreach) in reverse.
- require_detected_by_attribution — every flag must carry
  detected_by = ctx.instance_id so operators can audit the
  Verifier's track record per ADR-0036 §4.2.
- min_confidence_to_act = 0.80 (the load-bearing floor; matches
  the policy threshold exactly).
- out_of_scope: cross_agent_scan (v0.4 candidate per §6),
  modify_memory_entry, flag_below_confidence_floor.
- operator_duties: review track record periodically; re-birth
  or archive noisy Verifiers; treat flagged rows as proposals
  not findings (operator confirms via flagged_state lifecycle —
  schema v12 candidate, ADR-0036 T6 future work).
- drift_monitoring per_turn / max_deviation 0 / halt on drift —
  consistent with code_reviewer (also Guardian-genre).

Tests (+6 cases in test_constitution.py + 1 case in test_trait_engine.py):
- test_trait_engine: role count assertion bumped 17 -> 18; spot-
  check verifier_loop present.
- TestVerifierLoopRole class:
  * test_verifier_constitution_builds — constitution builds without
    error from the verifier_loop role.
  * test_verifier_role_base_policies_present — all three policy IDs
    (forbid_direct_memory_write / forbid_low_confidence_flag /
    require_detected_by_attribution) present.
  * test_verifier_min_confidence_floor — risk_thresholds.
    min_confidence_to_act == 0.80 (the §4.1 floor).
  * test_verifier_out_of_scope_includes_cross_agent — pins the v0.4
    scope boundary in the constitution itself.
  * test_verifier_drift_monitoring_strict — per_turn / 0 deviation /
    halt, consistent with code_reviewer.
  * test_verifier_birth_is_deterministic — same role + same profile
    = same constitution hash (Identity discipline holds for the new
    role same as every other).
- The existing test_all_five_roles_have_templates iterates every
  engine role through the constitution builder, so verifier_loop
  is automatically validated end-to-end (constitution builds,
  policies present, risk_thresholds set, drift_monitoring set)
  alongside the focused TestVerifierLoopRole assertions.

Test delta: 1968 -> 1974 passing (+6). Zero regressions.

Next: Burst 66 will ship ADR-0036 T2 (memory_flag_contradiction.v1
tool). The Verifier role is birthable now but has no flag-action
surface yet — T2 closes the 'minimum bar: Verifier exists, can
flag manually' milestone."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 65 landed. ADR-0036 T1 in production. Verifier role is birthable."
echo "Next: Burst 66 (memory_flag_contradiction.v1 tool — ADR-0036 T2)."
echo ""
read -rp "Press Enter to close..."
