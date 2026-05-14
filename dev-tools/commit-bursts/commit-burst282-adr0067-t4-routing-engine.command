#!/bin/bash
# Burst 282 — ADR-0067 T4: routing engine.
#
# Combines the three things needed to turn a sub-intent (output of
# decompose_intent.v1) into an actionable target_instance_id +
# skill_ref + cascade chain:
#   1. Domain registry (T1)
#   2. Handoffs catalog (this burst — config/handoffs.yaml)
#   3. Agent inventory (passed in by caller; T5 orchestrator will
#      query the live registry)
#
# What ships:
#
# 1. config/handoffs.yaml — engineer-edited handoff registry.
#    Two sections:
#      - default_skill_per_capability: (domain, capability) →
#        (skill_name, skill_version). Says "when routing to
#        capability X in domain Y, use skill Z." Seed with
#        mappings for D2/D3/D4/D8.
#      - cascade_rules: hardcoded follow-on routes. Seed with two
#        rules: PR review → compliance scan; incident response →
#        compliance evidence capture.
#
# 2. src/forest_soul_forge/core/routing_engine.py:
#    - SkillRef + Handoff + HandoffsConfig dataclasses
#    - ResolvedRoute (successful resolution payload) +
#      UnroutableSubIntent (failure with enum'd code)
#    - 5 reason codes: domain_not_found, domain_planned,
#      low_confidence, no_skill_mapping, no_alive_agent
#    - load_handoffs(path) → (config, errors). Missing file is
#      soft; malformed YAML / schema mismatch raises.
#    - resolve_route(subintent, registry, handoffs, agent_inventory)
#      → ResolvedRoute or UnroutableSubIntent. Pure function;
#      no global state. Resolution order: status gate → domain
#      exists → domain dispatchable → skill mapping → alive agent.
#    - apply_cascade_rules(decision, handoffs, registry, inventory)
#      → list of follow-on routes. Each cascade fires a NEW
#      resolve_route call, so cascade chains enforce the same
#      gates. No recursion: A→B fires, B→C does NOT chain.
#
# Tests (test_routing_engine.py — 15 cases):
#   load_handoffs:
#     - missing file soft / malformed hard / schema-mismatch hard
#     - happy path / per-rule errors soft
#   resolve_route failure codes:
#     - non-routable status (ambiguous → LOW_CONFIDENCE pass-through)
#     - domain_not_found
#     - domain_planned
#     - no_skill_mapping
#     - no_alive_agent
#   resolve_route happy paths:
#     - basic resolution
#     - picks the correct entry_agent role for the capability
#   apply_cascade_rules:
#     - cascade fires with follow-on
#     - cascade resolution failure returns UnroutableSubIntent
#       (never silent drop)
#     - no matching rule returns empty list
#   shipped seed validation:
#     - real config/handoffs.yaml loads with zero errors
#
# What's NOT in T4 (queued):
#   T4b: learned routes — operator-preference adaptation rail.
#        config/learned_routes.yaml + auto-edit by orchestrator +
#        Reality Anchor verification before activation. Layered
#        AFTER hardcoded; hardcoded always wins on conflict
#        (ADR-0072 discipline).
#   T5: domain_orchestrator agent role + singleton birth. Wires
#       decompose_intent → resolve_route → route_to_domain end-
#       to-end in its constitution.
#   T6: cross-domain handoff coordinator. For multi-domain
#       utterances ("remind me X AND draft Y"), sequence the
#       routes + aggregate results.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/handoffs.yaml \
        src/forest_soul_forge/core/routing_engine.py \
        tests/unit/test_routing_engine.py \
        dev-tools/commit-bursts/commit-burst282-adr0067-t4-routing-engine.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(orchestrator): ADR-0067 T4 — routing engine (B282)

Burst 282. Combines domain registry (T1) + handoffs catalog
(this burst) + agent inventory into resolve_route() that turns a
sub-intent into a ResolvedRoute (success) or UnroutableSubIntent
(enum'd failure).

What ships:

  - config/handoffs.yaml: engineer-edited handoff registry with
    schema_version 1. default_skill_per_capability section maps
    (domain, capability) → (skill_name, skill_version) so the
    resolver can fill in the skill ref route_to_domain.v1 needs.
    cascade_rules section captures hardcoded follow-on routes
    (PR review → compliance scan; incident response → compliance
    evidence capture).

  - core/routing_engine.py: pure-function routing layer. Frozen
    dataclasses for SkillRef, Handoff, HandoffsConfig,
    ResolvedRoute, UnroutableSubIntent. 5 enum'd failure codes.
    load_handoffs reads + validates the yaml (missing file soft;
    malformed / schema-mismatch hard). resolve_route returns a
    routing decision per sub-intent. apply_cascade_rules
    generates follow-on routes per the handoffs.yaml cascade
    rules. No recursion — cascades are one-step; operators write
    explicit rules for deeper chains.

Tests: test_routing_engine.py — 15 cases covering load happy
path / per-rule errors / hard failures, all 5 resolve_route
failure codes, 2 happy-path scenarios (basic + correct-role-for-
capability), cascade fires / cascade resolution failure /
no-matching-rule, real-seed validation.

Queued: T4b learned routes (operator-preference adaptation
layered after hardcoded; ADR-0072 discipline); T5 orchestrator
agent role + singleton birth; T6 cross-domain handoff
coordinator for multi-domain utterances."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 282 complete — ADR-0067 T4 routing engine shipped ==="
echo "Next: T5 domain_orchestrator agent role + singleton birth."
echo ""
echo "Press any key to close."
read -n 1
