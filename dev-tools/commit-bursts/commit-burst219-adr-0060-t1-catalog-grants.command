#!/bin/bash
# Burst 219 — ADR-0060 Accept + T1 (catalog_grants table + accessor).
#
# Three coupled landings:
#
# 1. ADR-0060 Draft → Accepted with the three open questions resolved:
#    - trust_tier default: yellow. Operators must pass green explicitly.
#    - T6 frontend: deferred to a separate burst after T2-T5.
#    - plugin_grants rename: declined; schemas differ enough that
#      unification would force awkward null columns.
#
# 2. Schema v16 → v17 migration plus matching fresh-install DDL.
#    New table agent_catalog_grants, keyed (instance_id, tool_name,
#    tool_version) with same trust_tier discipline as
#    agent_plugin_grants. idx_catalog_grants_active partial index
#    covers the per-dispatch "is this granted?" lookup that T2 will
#    add.
#
# 3. CatalogGrantsTable accessor at
#    src/forest_soul_forge/registry/tables/catalog_grants.py mirrors
#    PluginGrantsTable's surface:
#      - grant(instance_id, tool_name, tool_version, trust_tier, ...)
#      - revoke(instance_id, tool_name, tool_version, ...)
#      - get_active(instance_id, tool_name, tool_version)
#      - list_active / list_all
#      - active_tool_keys(instance_id) returns {name.vversion, ...}
#    Wired into Registry as self.catalog_grants.
#
# Plus: KNOWN_EVENT_TYPES gains agent_tool_granted +
# agent_tool_revoked. Catalog: 73 → 75 event types.
#
# This burst is INTENTIONALLY inert at dispatch — the dispatcher
# does not yet consult the new table. T2 (queued, next burst) is
# the load-bearing change. Shipping T1 alone is safe because:
#   - the table starts empty; no rows means no behavior change
#   - the accessor is unreferenced by the runtime path
#   - the registry test surface fully exercises the table CRUD
#
# Verification:
#   - 139 unit tests pass (registry + audit_chain + plugin_grants +
#     writes + tool_dispatch)
#   - Direct accessor smoke: grant → get → list → revoke → list_all
#     → idempotent re-revoke → ValueError on invalid trust_tier
#   - SCHEMA_VERSION bumped 16 → 17; test_registry's hardcoded ==16
#     assertions updated to ==17 (6 sites)
#
# What we deliberately did NOT do:
#   - T2 dispatcher integration. Load-bearing governance code;
#     needs its own focused burst with comprehensive tests so the
#     constitution gate's new branching doesn't silently let
#     wrong tools through.
#   - T3 endpoints (POST/DELETE/GET /agents/{id}/tools/grant).
#     Pointless without T2 — the grant rows exist but nothing
#     consults them. T3 lands once T2 makes the table consequential.
#   - T4-T6 posture × trust_tier matrix, tests, frontend. Each
#     independently shippable after T2.
#
# Per ADR-0001 D2: no identity surface touched. constitution_hash
#                  remains the agent's immutable root of authority.
# Per ADR-0044 D3: ABI grows additively — one new table, one new
#                  accessor namespace on Registry, two new audit
#                  event types. Zero existing call sites changed.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0060-runtime-tool-grants.md \
        src/forest_soul_forge/registry/schema.py \
        src/forest_soul_forge/registry/registry.py \
        src/forest_soul_forge/registry/tables/catalog_grants.py \
        src/forest_soul_forge/core/audit_chain.py \
        tests/unit/test_registry.py \
        dev-tools/commit-bursts/commit-burst219-adr-0060-t1-catalog-grants.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(registry): ADR-0060 Accept + T1 catalog_grants substrate (B219)

Burst 219. Lands T1 of ADR-0060 (Runtime Tool Grants) and flips the
ADR Draft -> Accepted.

Schema:
  v16 -> v17 migration + fresh-install DDL for agent_catalog_grants.
  Mirror of agent_plugin_grants keyed on
  (instance_id, tool_name, tool_version). idx_catalog_grants_active
  partial index for cheap per-dispatch lookups (T2 will use).

Accessor:
  registry/tables/catalog_grants.py — CatalogGrantsTable with the
  same surface as PluginGrantsTable: grant / revoke / get_active /
  list_active / list_all / active_tool_keys. Wired into Registry
  as self.catalog_grants.

Audit:
  KNOWN_EVENT_TYPES +2: agent_tool_granted, agent_tool_revoked.
  73 -> 75 event types.

INTENTIONALLY INERT at dispatch. T2 (queued) wires the
ConstitutionGateStep to consult get_active() on miss. T3-T6 follow.
Shipping T1 alone is safe because rows are empty and the accessor
has no caller in the runtime path yet.

Verification: 139 unit tests pass (registry, audit_chain,
plugin_grants, writes, tool_dispatch). Direct accessor smoke
exercises grant -> get -> list -> revoke -> idempotent re-revoke
-> ValueError on invalid trust_tier.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: ABI grows additively — new table, new accessor,
                 two new audit events. Zero existing call sites
                 changed."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 219 complete ==="
echo "=== ADR-0060 Accepted; T1 substrate live, inert until T2 wires the dispatcher. ==="
echo "Press any key to close."
read -n 1
