#!/bin/bash
# Burst 283 — ADR-0067 T5: domain_orchestrator role + singleton.
#
# The agent that wears the T2-T4 tools. New companion-genre role
# with decompose_intent.v1 + route_to_domain.v1 + operator_profile_read.v1
# + llm_think.v1 + delegate.v1 + verify_claim.v1 in its constitution.
# Singleton-per-forest like reality_anchor (ADR-0063 T4).
#
# What ships:
#
# 1. config/trait_tree.yaml: domain_orchestrator role_base entry.
#    Domain weights communication-heavy (2.5) + cognitive-high (2.0)
#    + security/audit elevated (1.8 / 1.6) for the tamper-evident
#    routing discipline. Emotional moderate (1.3) — the orchestrator
#    surfaces back on ambiguity, warmth matters. Embodiment minimum
#    floor (0.4).
#
# 2. config/genres.yaml: companion genre claims domain_orchestrator
#    alongside the other companion-class roles (assistant,
#    operator_companion, day_companion). Inherits the Companion-
#    genre risk floor (local providers, private memory ceiling).
#
# 3. config/constitution_templates.yaml: domain_orchestrator
#    template with:
#      - forbid_direct_action policy: orchestrator NEVER mutates
#        state directly. Every action goes through a downstream
#        delegate. Keeps the audit trail clean: every domain_routed
#        event pairs with an agent_delegated event, never with a
#        direct mutation from the router.
#      - forbid_self_delegate policy: routing must terminate.
#        Orchestrator can't route to itself or trigger recursive
#        cascades back to itself.
#      - allowed_tools: 7 tools (decompose + route + profile +
#        llm_think + delegate + verify_claim + memory_recall)
#      - reality_anchor.enabled=true: orchestrator's routing
#        decisions cross-checked against ground truth
#      - confidence_floor=0.6: default threshold, operator-tunable
#
# 4. src/forest_soul_forge/daemon/routers/writes/birth.py:
#    Singleton enforcement extended to a _SINGLETON_ROLES set
#    {reality_anchor, domain_orchestrator}. /birth refuses a
#    second active instance of either with 409. Operator archives
#    the existing one (POST /agents/archive) before spawning a
#    replacement. Mirrors the existing reality_anchor pattern
#    from B253.
#
# Tests (test_domain_orchestrator_role.py — 4 cases):
#   - trait_tree.yaml has the role with all 6 domain weights in
#     [0.4, 3.0] and communication highest
#   - genres.yaml companion genre claims domain_orchestrator
#   - constitution_templates.yaml has the template with required
#     policies + tools + reality_anchor.enabled
#   - birth.py source contains the literal _SINGLETON_ROLES set
#     including both reality_anchor and domain_orchestrator
#
# What's NOT in T5 (queued):
#   T6: cross-domain handoff coordinator for multi-domain utterances
#       ("remind me X AND draft Y AND tell me Z"). Sequences the
#       routes + aggregates results back to the operator.
#   T7: frontend Orchestrator pane — see routing decisions, tune
#       confidence threshold, edit handoffs.yaml from UI.
#   T8: /orchestrator/status health surface.
#   T4b: learned routes — operator-preference adaptation layer.
#
# After T5 the substrate is complete enough to birth a live
# orchestrator and have it dispatch end-to-end. The remaining
# tranches polish + ship operator UX on top.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/trait_tree.yaml \
        config/genres.yaml \
        config/constitution_templates.yaml \
        src/forest_soul_forge/daemon/routers/writes/birth.py \
        tests/unit/test_domain_orchestrator_role.py \
        dev-tools/commit-bursts/commit-burst283-adr0067-t5-orchestrator-role.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(orchestrator): ADR-0067 T5 — domain_orchestrator role + singleton (B283)

Burst 283. The agent that wears the T2-T4 tools. New
companion-genre singleton-per-forest role with
decompose_intent + route_to_domain + operator_profile_read +
llm_think + delegate + verify_claim in its constitution.

What ships:

  - config/trait_tree.yaml: domain_orchestrator role with
    communication-heavy weights (2.5) reflecting the
    intent-understanding + handoff orchestration job. Cognitive
    2.0 for LLM decomposition. Security 1.8 + audit 1.6 for the
    tamper-evident routing discipline. Emotional 1.3 (surfacing
    back on ambiguity). Embodiment 0.4 (validator floor).

  - config/genres.yaml: companion genre claims
    domain_orchestrator alongside assistant + operator_companion.
    Inherits the Companion-genre floor (local providers, private
    memory ceiling); decompose + route are read_only so they
    don't trip max_side_effects.

  - config/constitution_templates.yaml: role template with two
    forbid policies (direct_action — orchestrator NEVER mutates
    state, every action goes through delegate; self_delegate —
    routing must terminate, no recursive cascades), 7 allowed
    tools (decompose / route / profile / llm_think / delegate /
    verify_claim / memory_recall), reality_anchor.enabled=true
    (orchestrator's routing cross-checked against ground truth),
    confidence_floor=0.6 (operator-tunable default).

  - daemon/routers/writes/birth.py: singleton enforcement
    extended to _SINGLETON_ROLES={reality_anchor,
    domain_orchestrator}. /birth refuses a second active instance
    with 409; operator archives existing before spawning
    replacement. Mirrors B253 reality_anchor pattern.

Tests: test_domain_orchestrator_role.py — 4 cases. Trait_tree
weights in validator range + communication-highest; genres
claim; constitution template has required policies +
allowed_tools + reality_anchor enabled; birth.py source
contains _SINGLETON_ROLES with both roles.

After T5 the orchestrator can be birthed and dispatch end-to-end.
T6 (multi-domain coordinator), T7 (frontend pane), T8
(/orchestrator/status), T4b (learned routes) remain."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 283 complete — ADR-0067 T5 orchestrator role shipped ==="
echo "Next: T6 multi-domain handoff coordinator OR pause + birth a live"
echo "orchestrator instance to smoke-test the full dispatch chain."
echo ""
echo "Press any key to close."
read -n 1
