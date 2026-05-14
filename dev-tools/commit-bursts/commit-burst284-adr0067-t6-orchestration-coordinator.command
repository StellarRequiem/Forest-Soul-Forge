#!/bin/bash
# Burst 284 — ADR-0067 T6: multi-domain orchestration coordinator.
#
# Pure-function coordinator that turns an operator utterance like
# "remind me X AND draft Y AND tell me Z" into a sequenced dispatch
# across multiple domains, with operator-readable aggregation.
#
# Why a pure function not a tool:
# Tools are leaf operations in Forest's runtime. They shouldn't
# recursively dispatch other tools. The coordinator chains
# decompose → resolve_route → route per sub-intent, which would
# be a discipline violation if expressed as a single tool. Instead
# T6 ships this as a pure function the orchestrator agent (T5)
# invokes from inside a skill manifest. The skill's step DAG runs
# decompose_intent.v1 → for-each over sub-intents → route_to_domain.v1,
# with this coordinator providing the sequencing + bucketing.
#
# What ships:
#
# 1. src/forest_soul_forge/core/orchestration_coordinator.py:
#    - DispatchedRouteResult: frozen dataclass carrying the route +
#      downstream delegate outcome
#    - OrchestrationOutcome: frozen aggregate (utterance, three
#      buckets: dispatched / failed_dispatches / unroutable +
#      needs_operator_attention property)
#    - coordinate_dispatch(utterance, *, subintents OR decompose_fn,
#      registry, handoffs, agent_inventory, route_fn) → OrchestrationOutcome
#      Per-subintent: resolve_route → if Unroutable → unroutable
#      bucket; if Resolved → fire route_fn → succeeded:dispatched
#      OR failed:failed_dispatches → if succeeded also fire
#      cascade_rules and route each cascade
#    - _safe_route() wraps route_fn under try/except so a crashing
#      route_fn doesn't crash the coordinator — that route lands
#      in failed_dispatches with the error captured
#    - render_operator_summary(outcome) → Markdown the assistant
#      can read aloud or feed to a follow-up llm_think for natural-
#      language rephrasing
#
# 2. tests/unit/test_orchestration_coordinator.py: 11 cases
#    covering:
#      - happy path (3 sub-intents all dispatch cleanly)
#      - cascade firing (initial succeeds → cascade fires)
#      - failed dispatch bucketing
#      - unroutable subintent never reaches route_fn
#      - mixed outcome (one of each bucket)
#      - route_fn crash safety (lands in failed, never propagates)
#      - decompose_fn called when subintents=None
#      - no subintents + no decompose_fn → ValueError
#      - render summary: dispatched-only case + unroutable case
#
# What this enables:
#   T5's domain_orchestrator agent can now run a skill that calls
#   decompose_intent.v1 → for-each over the result → route_to_domain.v1
#   AND have the coordinator track everything that dispatched + what
#   needs operator clarification. The skill manifest itself ships
#   in a future burst (could be operator-authored via the existing
#   forge_skill flow, or pre-shipped as a canonical skill).
#
# What's NOT in T6:
#   - Parallel dispatch — coordinator is serial for audit chain
#     linearity. Parallelization is a future ADR.
#   - Retry policy — route_fn decides retries; coordinator only
#     records outcomes.
#   - Conversation runtime surfacing — caller (orchestrator skill)
#     takes OrchestrationOutcome and produces operator-facing text
#     using its own llm_think + voice.
#
# Three tranches remain in ADR-0067:
#   T4b: learned routes (operator-preference adaptation rail)
#   T7: frontend Orchestrator pane
#   T8: /orchestrator/status health surface

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/orchestration_coordinator.py \
        tests/unit/test_orchestration_coordinator.py \
        dev-tools/commit-bursts/commit-burst284-adr0067-t6-orchestration-coordinator.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(orchestrator): ADR-0067 T6 — multi-domain coordinator (B284)

Burst 284. Pure-function coordinator that sequences dispatch
across multiple sub-intents in a single operator utterance, with
operator-readable aggregation.

Why a pure function not a tool: tools are leaf operations in
Forest's runtime; they shouldn't recursively dispatch other tools.
The coordinator chains decompose → resolve_route → route per
sub-intent, which would be a discipline violation as a single
tool. The orchestrator agent (T5) invokes the coordinator from
inside a skill manifest's step DAG.

What ships:

  - core/orchestration_coordinator.py: DispatchedRouteResult +
    OrchestrationOutcome frozen dataclasses. coordinate_dispatch
    takes utterance + optional pre-supplied subintents OR a
    decompose_fn closure, registry, handoffs, agent_inventory,
    and a route_fn closure. Per-subintent: resolve_route classifies
    routable vs. unroutable; routable gets fired through route_fn
    and bucketed succeeded/failed; succeeded routes also fire
    cascade_rules. Three buckets returned: dispatched /
    failed_dispatches / unroutable. needs_operator_attention
    property flags whether the operator should see this outcome.

    _safe_route wraps route_fn under try/except so a crashing
    closure doesn't crash the coordinator — coordinator records
    the crash as a failed dispatch with error captured. Operator
    gets the full report.

    render_operator_summary produces Markdown the assistant can
    paste or feed to llm_think for voice-aware rephrasing.

Tests: test_orchestration_coordinator.py — 11 cases covering
happy path (3 sub-intents), cascade firing, failed dispatch
bucketing, unroutable never reaches route_fn, mixed outcome,
route_fn crash safety, decompose_fn vs pre-supplied subintents,
no-args ValueError, summary rendering.

After T6, the orchestrator substrate covers utterance →
dispatch end-to-end. Three tranches remain: T4b (learned routes),
T7 (frontend pane), T8 (health surface)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 284 complete — ADR-0067 T6 coordinator shipped ==="
echo "Next: T7 frontend Orchestrator pane OR T8 /orchestrator/status."
echo ""
echo "Press any key to close."
read -n 1
