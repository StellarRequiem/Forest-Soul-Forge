#!/bin/bash
# Burst 396 - ADR-0081 T3: wiring_sentinel role substrate.
#
# Third implementation tranche. Adds the guardian-genre singleton
# agent that runs the wiring audit. Mirrors reality_anchor +
# verifier_loop wiring (B331 / B252).
#
# What this commit adds:
#
# 1. config/tool_catalog.yaml — wiring_sentinel archetype with
#    read-only kit:
#      memory_recall.v1       look up prior audits
#      memory_write.v1        record audit outcome
#      audit_chain_verify.v1  verify chain before auditing
#      llm_think.v1           reason about severity + deltas
#      delegate.v1            escalate medium+ gaps
#      text_summarize.v1      compact coverage into punch list
#
# 2. config/trait_tree.yaml — wiring_sentinel trait profile.
#    Audit-heavy weights (audit=2.6 — the load-bearing trait),
#    communication=1.8 (operator-facing summaries are the
#    deliverable), security=1.6 (orphan tools are mild supply-
#    chain risk), cognitive=1.8 (diff prior audits against
#    current coverage), emotional + embodiment at validator
#    floor (cold-logic auditor).
#
# 3. config/constitution_templates.yaml — wiring_sentinel
#    role_base entry with four load-bearing policies:
#      forbid_substrate_mutation: ADR-0081 D5 — sentinel finds,
#        operator fixes. No filesystem_write / no
#        modify_tool_catalog / no modify_constitution / etc.
#      forbid_silent_audit: every run must emit at least one
#        chain event; silent runs let regressions hide.
#      require_chain_verify_before_audit: the chain is the
#        ground truth; if it's tampered with, diffs against
#        prior state are meaningless. Run audit_chain_verify.v1
#        first, gate every audit on a clean chain.
#      require_severity_classification: every gap escalation
#        must carry severity (info|low|medium|high). Operator
#        triage depends on this for the 4-hour cadence to not
#        become a notification firehose.
#    Risk thresholds mirror reality_anchor's tight gates:
#      auto_halt_risk: 0.50
#      escalate_risk: 0.20
#      min_confidence_to_act: 0.70
#    Operator duties: weekly review, advisory posture, re-birth
#    if false-positive > 5%, keep coverage.json fresh daily.
#
# 4. src/forest_soul_forge/daemon/routers/writes/birth.py —
#    SINGLETON_ROLES gains wiring_sentinel. /birth refuses a
#    second active wiring_sentinel with 409 (same pattern as
#    reality_anchor + domain_orchestrator).
#
# 5. dev-tools/birth-wiring-sentinel.command — 4-phase birth
#    driver. Mirrors birth-detection-engineer / birth-telemetry-
#    steward shape. Posture: GREEN at birth (no external reach,
#    pure read-only sentinel).
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: section-15 (T1) + wiring-coverage.html (T2) make
#     gaps visible on-demand, but operator-driven manual checks
#     don't scale. The sentinel + scheduled cadence (T5) is what
#     makes the gap class fail loud within a quarter-day, not at
#     the operator's next motivated review.
#   Prove non-load-bearing: archetype + trait profile + template
#     are ADDITIONS. Singleton-roles set ADDITION (wiring_sentinel
#     joins reality_anchor + domain_orchestrator). No removals.
#     Existing agents unaffected; only new wiring_sentinel births
#     are gated.
#   Prove alternative is strictly better:
#     (a) just run section-15 manually — what got us here.
#     (b) embed the audit into diagnostic-all (no agent) — no
#         lineage memory, no audit-chain attribution, no
#         operator-facing escalation path. The whole point of
#         ADR-0081 was to make this a first-class substrate
#         actor.
#     (c) actuator-genre that auto-fixes — would invert every
#         constitution-immutability invariant. ADR-0081 D5
#         explicitly forbids.
#
# Verification after this commit lands:
#   1. force-restart-daemon (loads the new archetype + template)
#   2. bash dev-tools/birth-wiring-sentinel.command
#      Expected: instance_id printed, constitution parses,
#      posture: green.
#   3. curl /agents | jq '.agents[] | select(.role=="wiring_sentinel")'
#      Expected: one row, status=active.
#   4. Birth a SECOND wiring_sentinel attempt
#      Expected: HTTP 409 with detail message about singleton.
#
# What this UNBLOCKS / queues next:
#   T4: wiring_audit.v1 skill (signature skill the sentinel runs).
#   T5: scheduled task + runbook (4-hour cadence via launchd).
#   T6: CLOSE - live verify + north-star + Accepted.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/tool_catalog.yaml \
        config/trait_tree.yaml \
        config/constitution_templates.yaml \
        src/forest_soul_forge/daemon/routers/writes/birth.py \
        dev-tools/birth-wiring-sentinel.command \
        dev-tools/commit-bursts/commit-burst396-adr0081-t3-wiring-sentinel-role.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(role): wiring_sentinel guardian-singleton substrate (ADR-0081 T3, B396)

Burst 396. Third implementation tranche of ADR-0081. Adds the
guardian-genre singleton agent that runs the 4-hour wiring audit.
Mirrors reality_anchor + verifier_loop wiring (B331/B252).

config/tool_catalog.yaml:
  wiring_sentinel archetype with read-only kit:
    memory_recall + memory_write + audit_chain_verify + llm_think
    + delegate + text_summarize.

config/trait_tree.yaml:
  wiring_sentinel trait profile. audit=2.6 (load-bearing),
  communication=1.8, cognitive=1.8, security=1.6,
  emotional+embodiment at validator floor.

config/constitution_templates.yaml:
  wiring_sentinel role_base with 4 policies:
    forbid_substrate_mutation: ADR-0081 D5 - sentinel finds,
      operator fixes. No filesystem_write, no modify_tool_catalog,
      no modify_constitution, etc.
    forbid_silent_audit: every run emits >=1 chain event.
    require_chain_verify_before_audit: chain is ground truth;
      diffs meaningless if tampered.
    require_severity_classification: every gap carries severity
      so the 4-hour cadence doesn't become a notification firehose.
  Risk thresholds mirror reality_anchor's tight gates.

birth.py:
  SINGLETON_ROLES gains wiring_sentinel. Second /birth attempt
  returns 409 (same pattern as reality_anchor + domain_orchestrator).

dev-tools/birth-wiring-sentinel.command:
  4-phase birth driver. Posture: GREEN at birth (no external
  reach, pure read-only sentinel).

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: section-15 + HTML on-demand views don't scale to
    'catch gaps within hours of introduction'. Sentinel + cadence
    is what closes that loop.
  Prove non-load-bearing: ADDITIONS only. Singleton-roles set
    gains one entry. Existing agents unaffected.
  Prove alternative is better: (a) manual run = what got us
    here; (b) headless audit = no lineage memory or chain attr;
    (c) auto-fix = inverts constitution-immutability invariant.

T4-T6 queued: wiring_audit.v1 skill -> scheduled task + runbook
-> CLOSE."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 396 complete - ADR-0081 T3 shipped ==="
echo "=========================================================="
echo "Verify:"
echo "  force-restart-daemon"
echo "  bash dev-tools/birth-wiring-sentinel.command"
echo "  curl -s http://127.0.0.1:7423/agents -H \"X-FSF-Token: \$FSF_API_TOKEN\" | jq '.agents[] | select(.role==\"wiring_sentinel\")'"
echo ""
echo "Next: T4 (wiring_audit.v1 skill)."
echo ""
echo "Press any key to close."
read -n 1 || true
