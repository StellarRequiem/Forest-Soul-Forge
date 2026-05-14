#!/bin/bash
# Burst 281 — ADR-0067 T3: route_to_domain.v1 builtin tool.
#
# Actuator side of cross-domain orchestration. Consumes the output
# of decompose_intent.v1 (T2 / B280), gates on target domain
# dispatchability, emits a domain_routed audit event BEFORE the
# delegate call, then fires delegate.v1 to invoke the target agent's
# skill.
#
# Why a distinct event_type:
# Cross-domain routing is its own audit concern (intent_hash +
# target_domain + capability + confidence) vs. agent delegation
# (caller + target + skill + reason). Splitting at the tool boundary
# keeps audit-chain queries clean. Both events fire for each
# successful route_to_domain call.
#
# Why this tool doesn't resolve target_instance_id:
# T3 ships the audited-routing primitive. The CALLER tells
# route_to_domain which instance. T4 (full routing engine) ships
# the resolver heuristic ("given a domain + capability, pick the
# alive agent instance to dispatch to") gated by handoffs.yaml +
# learned routes. T3 is the audit-clean wrapper around delegate.v1.
#
# What ships:
#
# 1. tools/builtin/route_to_domain.py:
#    - RouteToDomainTool (async). validate() requires 7 string fields
#      (target_domain, target_capability, target_instance_id, skill_name,
#      skill_version, intent, reason). Bounds intent (4000) + reason
#      (512). Confidence in [0,1].
#    - execute():
#        a. Load registry (refuses if not loadable)
#        b. Refuse if target_domain unknown
#        c. Refuse if domain.is_dispatchable=False (status=planned)
#           unless allow_planned=true (override recorded in audit)
#        d. Emit domain_routed audit event BEFORE delegate. Payload:
#           target_domain/capability/instance_id, intent_hash (NOT
#           raw intent), confidence, decomposition_seq, reason,
#           capability_known_in_registry, domain_status_at_route,
#           allow_planned_override
#        e. Fire ctx.delegate(...) — wraps delegate.v1 plumbing
#        f. Marshal outcome → status/delegate_output dict
#        g. Return ToolResult with PII-safe audit_payload (intent_hash,
#           never raw intent)
#
# 2. core/audit_chain.py: register 'domain_routed' in KNOWN_EVENT_TYPES
#    with full event_data schema documented in the file. Verifier
#    accepts the new event type; existing chains stay valid.
#
# 3. config/tool_catalog.yaml: register route_to_domain.v1 with
#    side_effects=read_only (the routing itself is read-only; the
#    downstream delegated skill carries its own side-effect tier)
#    and archetype_tags=[companion, assistant].
#
# 4. tools/builtin/__init__.py: import + register RouteToDomainTool
#    in the builtin registry init.
#
# Tests (test_route_to_domain.py — 13 cases):
#   Validation:
#     - all required fields enforced (parametrized over 7 fields)
#     - intent length ceiling
#     - confidence range [0,1]
#   Domain gating:
#     - unknown domain refuses with "not in registry" message
#     - planned domain refuses by default
#     - planned with allow_planned=true routes, override flag in audit
#   Audit event emission:
#     - domain_routed fires BEFORE delegate
#     - payload contains intent_hash (NOT raw intent — PII safety)
#     - capability_known_in_registry marked false for off-catalog cap
#   Outcome marshaling:
#     - succeeded outcome → ToolResult.success=True + delegate_output
#     - failed outcome → ToolResult.success=False + failure_reason
#   Failure modes:
#     - DelegateError mid-route → ToolValidationError("delegate refused")
#     - ctx.delegate=None → ToolValidationError("no delegator")
#
# What's NOT in T3 (queued):
#   T4: full routing engine — handoffs.yaml hardcoded rail + learned
#       routes adapter + agent-instance resolver
#   T5: domain_orchestrator agent role + singleton birth
#   T6: cross-domain handoff coordinator (multi-domain dispatch)

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/builtin/route_to_domain.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        src/forest_soul_forge/core/audit_chain.py \
        config/tool_catalog.yaml \
        tests/unit/test_route_to_domain.py \
        dev-tools/commit-bursts/commit-burst281-adr0067-t3-route-to-domain.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(orchestrator): ADR-0067 T3 — route_to_domain.v1 tool (B281)

Burst 281. Actuator side of cross-domain orchestration. Consumes
decompose_intent.v1 (T2) output, emits domain_routed audit event,
fires delegate.v1 to invoke the target agent's skill.

What ships:

  - tools/builtin/route_to_domain.py: RouteToDomainTool (async).
    Validates 7 required string fields. Loads domain registry,
    refuses on unknown target_domain OR on status=planned (unless
    allow_planned=true, recorded as override). Emits domain_routed
    audit event BEFORE the delegate so orchestrator intent is
    captured independent of downstream outcome. Fires ctx.delegate;
    marshals outcome to flat dict. PII-safe audit_payload: intent_hash,
    never raw intent text.

  - core/audit_chain.py: register 'domain_routed' in
    KNOWN_EVENT_TYPES with documented schema (target_domain +
    capability + instance_id + intent_hash + confidence +
    decomposition_seq + reason + override flags).

  - config/tool_catalog.yaml: register route_to_domain.v1 with
    archetype_tags=[companion, assistant]. side_effects=read_only —
    the routing IS read-only; downstream delegated skills carry
    their own side-effect tier.

  - tools/builtin/__init__.py: import + register RouteToDomainTool
    in the builtin registry init.

Required initiative L3 (mirror of delegate.v1) — reactive Companion
(L1) can't autonomously route across domains.

Tests: test_route_to_domain.py — 13 cases covering validation
(parametrized over 7 required fields + length + range), domain
gating (unknown / planned / planned-with-override), audit event
shape (event fires before delegate, intent_hash NOT raw text,
capability_known flag), outcome marshaling (succeeded/failed),
failure modes (DelegateError, no delegator wired).

After T3 the cross-domain orchestrator's tool surface is complete
(decompose + route). T4 ships the routing engine that picks
target_instance_id automatically; T5 ships the orchestrator agent
role with these tools in its constitution."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 281 complete — ADR-0067 T3 route_to_domain shipped ==="
echo "Next: T4 routing engine (handoffs.yaml + agent resolver)."
echo ""
echo "Press any key to close."
read -n 1
