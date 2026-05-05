#!/bin/bash
# Burst 114 — ADR-0045 T1: posture substrate.
#
# Schema v14 → v15 + new agents.posture column + PostureGateStep
# at the END of the governance pipeline (outermost authority).
# T1 enforces only the agent-wide posture; per-grant trust_tier
# enforcement is forward-compat and ships in T3 (Burst 115).
#
# What ships:
#
#   Schema v15 (ALTER TABLE agents ADD COLUMN posture):
#     - DEFAULT 'yellow' — matches the de-facto behavior of existing
#       agents (per-tool config gates mutating ops; yellow doesn't
#       override that). Migration is a semantic no-op for existing
#       rows.
#     - CHECK (posture IN ('green', 'yellow', 'red')).
#     - idx_agents_posture for cheap operator queries.
#     - test_v10_to_v11_forward_migration test fixture extended to
#       drop posture+v14 grants table+v13 scheduler table when
#       stamping the DB back to v10 (so the reapplied migrations
#       don't trip on already-existing columns).
#
#   PostureGateStep at end of governance pipeline:
#     - Sits AFTER ApprovalGateStep — outermost authority. Can
#       override an upstream GO with REFUSE (red) or PENDING
#       (yellow) for non-read-only side_effects.
#     - Read-only tools always pass through regardless of posture.
#       Red blocks ACTIONS, not THINKING — agent can still
#       memory_recall, code_read, llm_think.
#     - enforce_per_grant flag (default False) is the T3 hook.
#       Burst 115 flips it on and the per-grant trust_tier from the
#       agent_plugin_grants table starts overriding agent posture
#       for mcp_call.v1 dispatches.
#
#   DispatchContext.agent_posture + DispatchContext.plugin_grants_view:
#     - Populated by the dispatcher BEFORE pipeline.run() via
#       _load_agent_posture() and _load_plugin_grants_view()
#       helpers.
#     - None when agent_registry / plugin_grants are unwired
#       (test contexts) — step short-circuits to GO.
#     - plugin_grants_view is loaded today even though the step
#       doesn't consume it (forward-compat for T3); same posture
#       as the merged_mcp_registry pre-load from Burst 111.
#
# Verification:
#   - tests/unit/test_posture_gate_step.py — 15 new tests:
#     - 3 schema tests (column exists, defaults to yellow, CHECK
#       constraint blocks invalid values)
#     - 12 step semantics tests covering the matrix:
#         green / yellow / red × external / filesystem / network /
#         read_only, plus None-posture short-circuit, resolved
#         side_effects override, T3 forward-compat hook stays
#         dormant when enforce_per_grant=False.
#   - Schema-stamp-back fixture extended for v15.
#   - Full unit suite: 2,335 → 2,350 (+15, zero regressions).
#
# Outstanding for the ADR-0045 arc:
#   - T2 (Burst 114b): HTTP POST /agents/{id}/posture + CLI +
#     frontend dial + agent_posture_changed audit event.
#   - T3 (Burst 115): flip enforce_per_grant=True, implement the
#     red-dominates precedence matrix, ship the 3×3 per-grant ×
#     posture combination tests.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/registry/schema.py
git add src/forest_soul_forge/tools/governance_pipeline.py
git add src/forest_soul_forge/tools/dispatcher.py
git add tests/unit/test_posture_gate_step.py
git add tests/unit/test_registry.py
git add tests/unit/test_daemon_readonly.py
git add tests/unit/test_plugin_grants.py

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(posture): schema v15 + agents.posture + PostureGateStep agent-only (ADR-0045 T1)

Burst 114 — first implementation tranche of ADR-0045 Agent Posture
/ Trust-Light System. Adds the substrate for the green/yellow/red
per-agent posture dial.

What ships:

- Schema v14 → v15: ALTER TABLE agents ADD COLUMN posture TEXT
  NOT NULL DEFAULT 'yellow' CHECK (posture IN
  ('green', 'yellow', 'red')) plus idx_agents_posture. Pure
  addition; existing rows take the 'yellow' default which matches
  the pre-Burst-114 de-facto behavior (per-tool config gates
  mutating ops; yellow doesn't override that). Migration is a
  semantic no-op for existing agents.

- PostureGateStep added at the END of the governance pipeline.
  Outermost authority — sits AFTER ApprovalGateStep so it can
  override an upstream GO with REFUSE (red) or PENDING (yellow)
  for non-read-only side_effects. Read-only ALWAYS bypasses
  posture; red blocks ACTIONS not THINKING (agent can still
  memory_recall, code_read, llm_think).

- DispatchContext gets agent_posture + plugin_grants_view fields,
  populated by the dispatcher BEFORE pipeline.run() via
  _load_agent_posture() and _load_plugin_grants_view() helpers.
  Same shape as the Burst 111 merged_mcp_registry pre-load.
  None-on-unwired keeps test contexts free.

- enforce_per_grant flag on PostureGateStep (default False) is
  the T3 forward-compat hook. plugin_grants_view is loaded today
  even though the step doesn't consume it; Burst 115 flips
  enforce_per_grant=True and the per-grant trust_tier starts
  overriding agent posture for mcp_call.v1 dispatches.

- test_v10_to_v11 stamp-back fixture extended to drop posture
  column + v14 grants table + v13 scheduler-state table when
  reverting the DB to v10, so the reapplied migrations don't
  trip on already-existing columns. Production migration path is
  unaffected.

Verification:
- tests/unit/test_posture_gate_step.py — 15 new tests covering
  schema (column exists / default yellow / CHECK constraint) and
  step semantics (3 postures × 4 side-effects + None short-circuit
  + resolved override + T3 hook stays dormant at default).
- Full unit suite: 2,335 → 2,350 (+15, zero regressions).

Outstanding for ADR-0045 arc:
- T2 (Burst 114b): HTTP POST /agents/{id}/posture + CLI + frontend
  dial + agent_posture_changed audit event.
- T3 (Burst 115): enforce_per_grant=True + red-dominates precedence
  matrix + 3×3 combinatorial tests."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 114 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
