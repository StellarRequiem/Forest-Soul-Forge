#!/bin/bash
# Burst 115 — ADR-0045 T3+T4: per-grant trust_tier enforcement +
# precedence matrix tests.
#
# Closes the ADR-0045 implementation arc. Burst 114 shipped agent-
# only posture; this burst flips PostureGateStep.enforce_per_grant
# on at the dispatcher level and turns the trust_tier field on
# agent_plugin_grants from forward-compat storage into live
# enforcement.
#
# What ships:
#
#   src/forest_soul_forge/tools/governance_pipeline.py:
#     - PostureGateStep.evaluate() rewritten to compute effective
#       posture per ADR-0045 §"Interaction with per-grant
#       trust_tier":
#         1. Default = agent posture.
#         2. If mcp_call.v1 + grant exists for this server →
#            fold per-grant tier in via red-dominates rule.
#         3. Special downgrade: agent yellow + grant green for
#            THIS plugin = green for THIS mcp_call (operator
#            explicitly vouched for the server).
#         4. Resolve to GO / PENDING / REFUSE.
#     - The earlier short-circuit `if posture == 'green' return
#       GO` removed — green agents now have to flow through the
#       per-grant fold so a yellow/red GRANT on a specific plugin
#       can still escalate.
#
#   src/forest_soul_forge/tools/dispatcher.py:
#     - PostureGateStep(enforce_per_grant=True) at the end of the
#       governance pipeline. Burst 114 had enforce_per_grant=False;
#       T3 turns it on.
#
# Verification:
#   - tests/unit/test_posture_per_grant.py — 28 new tests:
#     - 9 precedence matrix combinations (3 agent × 3 grant)
#     - 3 no-grant fallback cases (matches Burst 114 agent-only)
#     - 12 read-only short-circuits (parametrized 3×4)
#     - 2 non-mcp_call ignore-per-grant cases
#     - 2 grant-plugin-isolation cases (grant on plugin X doesn't
#       affect dispatch to plugin Y)
#   - Existing test_posture_gate_step.py still 15/15 (the
#     enforce_per_grant=False default path unchanged).
#   - Full unit suite: 2,358 → 2,386 (+28, zero regressions).
#
# This closes ADR-0045 implementation-complete. The traffic-light
# system is now enforced end-to-end:
#   - Schema (v15 agents.posture column) — Burst 114
#   - Dispatcher enforcement (PostureGateStep) — Burst 114 (agent)
#     + Burst 115 (per-grant)
#   - HTTP + CLI + audit (agent_posture_changed) — Burst 114b
#   - Per-agent + per-(agent,plugin) precedence — Burst 115
#
# Outstanding from ADR-0045 (deferred to amendments):
#   - Operator-session posture (global walk-away dial)
#   - Programmatic posture changes (verifier-driven self-demotion)
#   - Time-bounded posture
#   - Multi-operator audit policy on red→green transitions
#   - Frontend dial widget (deferred from Burst 114b)

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/governance_pipeline.py
git add src/forest_soul_forge/tools/dispatcher.py
git add tests/unit/test_posture_per_grant.py

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(posture): per-grant trust_tier enforcement + precedence matrix (ADR-0045 T3+T4)

Burst 115. Closes the ADR-0045 implementation arc by flipping
PostureGateStep.enforce_per_grant=True at the dispatcher level and
turning the agent_plugin_grants.trust_tier field from forward-compat
storage (Burst 113a) into live enforcement.

What changes:

- PostureGateStep.evaluate() rewritten to compute effective posture
  per ADR-0045 §'Interaction with per-grant trust_tier':
    1. Default = agent posture.
    2. If mcp_call.v1 + grant exists for this server → fold per-
       grant tier in via red-dominates rule.
    3. Special downgrade: agent yellow + grant green for THIS
       plugin = green for THIS mcp_call (operator explicitly
       vouched for the server).
    4. Resolve to GO / PENDING / REFUSE.
  Earlier short-circuit 'if posture == green: return GO' removed
  so a green agent with a yellow/red grant on a specific plugin
  still escalates appropriately.

- Dispatcher pipeline: PostureGateStep(enforce_per_grant=True).
  Burst 114 had enforce_per_grant=False (agent-only); T3 turns the
  per-grant interaction on.

The 3×3 precedence matrix:

    agent →    │ green       │ yellow      │ red
    grant ↓    │             │             │
    ───────────┼─────────────┼─────────────┼────────────
    green      │ GO          │ GO (1)      │ REFUSE
    yellow     │ PENDING     │ PENDING     │ REFUSE
    red        │ REFUSE      │ REFUSE      │ REFUSE
    none       │ GO          │ PENDING     │ REFUSE

(1) Per-grant green for THIS specific plugin downgrades agent-
    yellow gating — operator explicitly vouched for the server.

Verification:
- tests/unit/test_posture_per_grant.py — 28 new tests:
  - 9 precedence matrix combinations (3 agent × 3 grant)
  - 3 no-grant fallback cases (matches Burst 114 agent-only)
  - 12 read-only short-circuits (parametrized 3×4)
  - 2 non-mcp_call ignore-per-grant cases
  - 2 grant-plugin-isolation cases
- Existing 15 tests in test_posture_gate_step.py unchanged.
- Full suite: 2,358 → 2,386 (+28, zero regressions).

This closes ADR-0045 implementation-complete:
  Schema (v15 agents.posture)         — Burst 114
  PostureGateStep (agent-only)        — Burst 114
  HTTP + CLI + audit event            — Burst 114b
  Per-grant enforcement + precedence  — Burst 115

Deferred to ADR-0045 amendments:
- Operator-session posture (global walk-away dial)
- Programmatic posture changes (verifier-driven self-demotion)
- Time-bounded posture
- Multi-operator audit policy on red→green
- Frontend dial widget (deferred from 114b — pure UI, no backend
  coupling)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 115 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
