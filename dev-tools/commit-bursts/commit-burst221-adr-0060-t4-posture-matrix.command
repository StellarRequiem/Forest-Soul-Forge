#!/bin/bash
# Burst 221 — ADR-0060 T4 posture × trust_tier interaction matrix.
#
# ADR-0060 D4 specifies a 9-cell matrix for grant-sourced dispatches:
#
#   | Agent  | green grant | yellow grant | red grant |
#   |--------|-------------|--------------|-----------|
#   | green  | GO          | GO           | GO        |
#   | yellow | GO          | GO           | PENDING   |
#   | red    | PENDING     | PENDING      | REFUSE    |
#
# Pre-B221 the dispatcher consulted catalog grants (B220 T2/T3) but
# the grant's trust_tier was inert — every successful grant produced
# a GO verdict at PostureGateStep regardless of the matrix. B221
# wires the matrix.
#
# Implementation:
#
#   1. DispatchContext gains ``granted_trust_tier: str | None``.
#      Set by ConstraintResolutionStep alongside granted_via /
#      grant_seq when the grant lookup hits.
#
#   2. ``_lookup_catalog_grant`` return shape grows from
#      ``(resolved, granted_at_seq)`` to
#      ``(resolved, granted_at_seq, trust_tier)``. The accessor
#      already stored the tier; we just thread it through.
#
#   3. PostureGateStep gains a new branch: when
#      ``dctx.granted_trust_tier is not None``, the matrix decision
#      is the COMPLETE decision for that dispatch — bypasses the
#      agent-level branching below. Three new gate_source values:
#        - posture_yellow_grant_red  (PENDING)
#        - posture_red_grant_lower   (PENDING; covers green+red and
#                                     yellow+red agent-grant pairs)
#        - agent_posture_red_grant_red (REFUSE — doubly-defended)
#
# The matrix is intentionally MORE PERMISSIVE than the plugin_grants
# red-dominates precedence rule. Rationale (ADR-0060 D4): an operator
# granting a specific (tool, agent) pair has explicitly signaled
# trust at the grant's tier — that signal is load-bearing and shifts
# the posture threshold for THIS tool, on THIS agent. A green-tier
# grant on a yellow-postured agent is "I trust this exact combination
# fully" and goes through; a red-tier grant on a red agent is
# doubly-defended and refuses.
#
# Verification:
#   - 170 unit tests pass (tool_dispatch, writes, plugin_grants,
#     audit_chain, posture_per_grant, posture_gate_step).
#   - In-process matrix smoke exercises all 9 cells of the D4
#     matrix against PostureGateStep with mocked tool/resolved;
#     every cell returns the expected verdict.
#
# What we deliberately did NOT do:
#   - Modify the plugin_grants enforce_per_grant code path. That
#     uses its own red-dominates precedence rule (Burst 115);
#     ADR-0060 grants use a different, more permissive matrix.
#     Both rules coexist — they apply to different grant types.
#   - Audit-event tagging when the matrix downgrades a verdict.
#     The existing tool_call_dispatched event includes
#     applied_rules=(granted_via:catalog_grant,) so an auditor
#     can already identify grant-sourced dispatches. The verdict
#     itself is already in the chain via the standard pending /
#     refuse events.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: PostureGateStep behavior unchanged when
#                  granted_trust_tier is None (pre-B221 path).
#                  ABI grows additively — one new DispatchContext
#                  field, three new gate_source string values.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/governance_pipeline.py \
        src/forest_soul_forge/tools/dispatcher.py \
        dev-tools/commit-bursts/commit-burst221-adr-0060-t4-posture-matrix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(posture): ADR-0060 T4 — posture x trust_tier matrix (B221)

Burst 221. Wires the ADR-0060 D4 interaction matrix for grant-sourced
dispatches. Pre-B221 the grant's trust_tier was stored but inert at
the posture gate; B221 makes it consequential.

Matrix:
  | Agent  | green grant | yellow grant | red grant |
  |--------|-------------|--------------|-----------|
  | green  | GO          | GO           | GO        |
  | yellow | GO          | GO           | PENDING   |
  | red    | PENDING     | PENDING      | REFUSE    |

Implementation:
  - DispatchContext.granted_trust_tier threads the grant's tier
    through from ConstraintResolutionStep to PostureGateStep.
  - _lookup_catalog_grant return shape grows to include tier.
  - PostureGateStep gains a complete branch for grant-sourced
    dispatches with three new gate_source values:
      posture_yellow_grant_red
      posture_red_grant_lower
      agent_posture_red_grant_red

The matrix is more permissive than plugin_grants' red-dominates
rule. Operator who granted (tool, agent) pair explicitly signaled
trust — that signal shifts the threshold for THIS dispatch.

Verification:
  - 170 unit tests pass.
  - In-process matrix smoke exercises all 9 cells; each returns
    the expected verdict.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: legacy behavior unchanged when granted_trust_tier
                 is None. ABI grows additively."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 221 complete ==="
echo "=== ADR-0060 D4 matrix live; grant trust_tier consequential at the posture gate. ==="
echo "Press any key to close."
read -n 1
