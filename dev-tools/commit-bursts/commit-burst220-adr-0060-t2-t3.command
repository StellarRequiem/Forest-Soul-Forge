#!/bin/bash
# Burst 220 — ADR-0060 T2 dispatcher integration + T3 endpoints.
#
# Closes the natural-language Forge UI loop. After today, an
# operator can:
#   1. Forge a new tool via /tools/forge (Bursts 202+)
#   2. Grant it to an existing agent via POST
#      /agents/{id}/tools/grant (this burst)
#   3. Dispatch the granted tool from that agent — the dispatch
#      flows past the constitution gate via the new runtime-grant
#      lookup. (this burst)
#
# Before B220, step 3 required re-birthing the agent (destroying
# the lineage). After B220, the constitution_hash stays immutable
# while the effective tool surface expands at runtime.
#
# T2 — dispatcher integration:
#
#   ConstraintResolutionStep gains an optional
#   ``catalog_grant_lookup_fn`` parameter. When the constitution
#   lookup misses and the lookup fn returns a grant, the step
#   substitutes catalog-default constraints (side_effects from the
#   ToolDef; empty constraints dict; applied_rules carries
#   ``"granted_via:catalog_grant"`` so an auditor can identify
#   grant-sourced dispatches in the chain).
#
#   DispatchContext gains ``granted_via`` + ``grant_seq`` fields
#   for downstream audit annotation. The applied_rules tuple
#   already lands in tool_call_dispatched events via the existing
#   emission path, so an auditor querying
#   "what was dispatched via runtime grants on this agent?" can
#   filter on applied_rules containing "granted_via:catalog_grant".
#
#   ToolDispatcher gains two new fields:
#     - catalog_grants — the CatalogGrantsTable from B219 (T1)
#     - tool_catalog — for side_effects default lookup
#
#   The dispatcher's _lookup_catalog_grant() helper composes both
#   into the closure passed to ConstraintResolutionStep. None
#   catalog_grants (test contexts) makes the step fall through to
#   the pre-B220 refuse path.
#
# T3 — endpoints:
#
#   New router src/forest_soul_forge/daemon/routers/catalog_grants.py
#   with three routes:
#     POST   /agents/{instance_id}/tools/grant
#            body: {tool_name, tool_version, trust_tier?, reason?}
#            Validates (name, version) exists in tool_catalog per
#            ADR-0060 D5; 400 on unknown refs. trust_tier defaults
#            to "yellow"; operator must pass "green" explicitly.
#     DELETE /agents/{instance_id}/tools/grant/{tool_name}/{tool_version}
#            Idempotent per ADR-0060 D3 — revoking already-revoked
#            returns 200 {ok:true, no_op:true} rather than 404.
#     GET    /agents/{instance_id}/tools/grants
#            ?history=true includes revoked rows.
#
#   Both mutations hold write_lock and emit chain events:
#     agent_tool_granted  — {trust_tier, granted_by, reason, ...}
#     agent_tool_revoked  — includes the original granted_at_seq
#                           for lineage so an auditor can trace
#                           the grant's lifecycle from one row.
#
# Smoke verification (in-process TestClient):
#   1. Birth a translator agent (no audit_chain_verify in archetype)
#   2. Dispatch audit_chain_verify.v1 → 404 tool_not_in_constitution
#   3. POST grant → 200, granted_at_seq emitted in chain
#   4. GET grants → count=1
#   5. Dispatch audit_chain_verify.v1 → 200 (flows past gate;
#      fails at runtime on missing args — DIFFERENT layer; the
#      constitution gate let it through, which is the proof T2
#      works)
#   6. DELETE → 200, revoked_at_seq emitted
#   7. DELETE again → 200, {no_op:true} — idempotent
#   8. Dispatch audit_chain_verify.v1 → 404 tool_not_in_constitution
#      again. Grant gone, gate restored.
#
# 116 unit tests pass (tool_dispatch + writes + plugin_grants +
# audit_chain) — no regressions in the existing surface.
#
# What we deliberately did NOT do:
#   - T4 posture x trust_tier interaction matrix. ADR-0060 D4
#     specifies the matrix but PostureGateStep doesn't yet
#     consult catalog grants. Today every grant behaves as the
#     stored trust_tier value. T4 is its own focused burst.
#   - T5 comprehensive unit tests. The in-process smoke proves
#     the end-to-end path; per-step unit tests are a follow-up.
#   - T6 frontend grants pane. Mechanical wiring after T4 lands.
#
# Per ADR-0001 D2: constitution_hash remains the agent's
#                  immutable root of authority. Grants augment;
#                  they never mutate.
# Per ADR-0044 D3: ABI grows additively — three new endpoints,
#                  two new audit event types (registered in
#                  B219), two new DispatchContext fields. Zero
#                  existing call sites changed.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/dispatcher.py \
        src/forest_soul_forge/tools/governance_pipeline.py \
        src/forest_soul_forge/daemon/deps.py \
        src/forest_soul_forge/daemon/app.py \
        src/forest_soul_forge/daemon/routers/catalog_grants.py \
        dev-tools/commit-bursts/commit-burst220-adr-0060-t2-t3.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(dispatcher,daemon): ADR-0060 T2+T3 runtime grants live (B220)

Burst 220. Closes the natural-language Forge UI loop. An operator
can now forge a tool, grant it to an existing agent without
re-birth, and dispatch — all under audit, with the constitution
hash unchanged.

T2 — dispatcher integration:
  ConstraintResolutionStep gains an optional catalog_grant_lookup_fn.
  On constitution miss, the step consults agent_catalog_grants;
  active grants substitute catalog-default constraints with
  applied_rules=(granted_via:catalog_grant,) for chain identification.
  DispatchContext gains granted_via + grant_seq fields. ToolDispatcher
  gains catalog_grants + tool_catalog fields wired in deps.py.
  None paths (test contexts) preserve pre-B220 refuse behavior.

T3 — endpoints (new router catalog_grants.py):
  POST   /agents/{instance_id}/tools/grant
         {tool_name, tool_version, trust_tier?, reason?}
         Validates catalog membership per ADR-0060 D5.
  DELETE /agents/{instance_id}/tools/grant/{name}/{version}
         Idempotent per ADR-0060 D3.
  GET    /agents/{instance_id}/tools/grants?history=false

Audit emissions: agent_tool_granted / agent_tool_revoked
(both registered in KNOWN_EVENT_TYPES in B219).

Verification:
  - 116 unit tests pass (tool_dispatch, writes, plugin_grants,
    audit_chain). No regressions.
  - In-process TestClient smoke runs the full state machine:
    birth -> refuse -> grant -> 200 dispatch through the gate ->
    list -> revoke -> idempotent re-revoke -> refuse again.

What's queued:
  - T4 PostureGateStep consults catalog grants for the trust_tier
    matrix (ADR-0060 D4)
  - T5 comprehensive unit tests beyond the smoke
  - T6 frontend grants pane

Per ADR-0001 D2: constitution_hash immutable; grants augment only.
Per ADR-0044 D3: ABI grows additively — three new endpoints, two
                 new DispatchContext fields, zero call-site
                 changes."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 220 complete ==="
echo "=== Runtime tool grants live end-to-end. Forge -> grant -> dispatch loop CLOSED. ==="
echo "Press any key to close."
read -n 1
