#!/bin/bash
# Burst 238 — ADR-0053 T3: HTTP API for per-tool plugin grants.
#
# Extends the operator surface:
#   POST /agents/{id}/plugin-grants
#     body: {plugin_name, tool_name?, trust_tier?, reason?}
#     - tool_name=null/omitted: plugin-level grant (ADR-0043
#       original semantic). Existing behavior, byte-for-byte.
#     - tool_name=string: per-tool grant covering only that
#       one tool (ADR-0053 D2 + D3).
#   DELETE /agents/{id}/plugin-grants/{plugin_name}
#     Plugin-level revoke — unchanged shape. Targets ONLY the
#     plugin-level row at (agent, plugin); per-tool grants on
#     the same plugin are untouched.
#   DELETE /agents/{id}/plugin-grants/{plugin_name}/tools/{tool}
#     NEW. Per-tool revoke. Targets the exact triple. Returns
#     404 if no per-tool active grant exists at the triple
#     (does NOT fall back to plugin-level — operator can't
#     accidentally revoke broader access through a per-tool
#     URL).
#   GET /agents/{id}/plugin-grants
#     Response rows gain `tool_name` field (null for plugin-
#     level, string for per-tool). Pre-B238 clients that
#     don't read the field still work.
#
# Audit events (ADR-0053 D4):
#   `agent_plugin_granted` / `agent_plugin_revoked` event_data
#   gain optional `tool_name` field. Same event_type used for
#   both grant shapes so an auditor querying
#   `event_type = 'agent_plugin_granted'` gets the full
#   chronological view; filtering by `tool_name` is secondary.
#
# Side cleanup: registered `agent_plugin_revoked` in
# `KNOWN_EVENT_TYPES`. Pre-existing gap that warned at every
# chain verification — fits B238 scope since I'm touching the
# same surface.
#
# Files touched:
#
# 1. src/forest_soul_forge/daemon/routers/plugin_grants.py
#    - Module docstring expanded with the per-tool surface
#      and ADR-0053 D4 audit-event semantics.
#    - GrantRequest gains optional tool_name field (Pydantic
#      validation, max_length=120 to match tool identifier
#      shape, null/omit = plugin-level).
#    - _serialize_grant exposes tool_name on the wire.
#    - POST grant_plugin passes tool_name through to the
#      table + emits it in agent_plugin_granted event_data.
#    - DELETE refactored into _do_revoke helper used by both
#      the existing plugin-level route and the new per-tool
#      route. Shares pre-check + audit-emission + revoke flow.
#    - NEW: DELETE /agents/{id}/plugin-grants/{plugin}/tools/{tool}
#      route.
#
# 2. src/forest_soul_forge/core/audit_chain.py
#    - Register `agent_plugin_revoked` in KNOWN_EVENT_TYPES
#      (pre-existing gap). Doc comment on the registration
#      explains the per-tool extension semantic.
#
# 3. tests/unit/test_daemon_plugin_grants.py
#    - 8 new endpoint tests for the per-tool path:
#        post_with_tool_name_creates_per_tool_grant
#        post_per_tool_emits_audit_event_with_tool_name
#        plugin_level_and_per_tool_grants_coexist
#        delete_per_tool_route_revokes_only_that_tool
#        delete_plugin_level_route_does_not_touch_per_tool
#        delete_per_tool_404_when_no_such_grant
#        delete_per_tool_emits_audit_event_with_tool_name
#        get_response_includes_tool_name_per_row
#    - 13 pre-existing daemon-side tests stay green.
#
# 4. docs/decisions/ADR-0053-per-tool-plugin-grants.md
#    - Status block bumped to "T1 + T2 + T3 shipped".
#    - Tranche table marks T3 DONE with full implementation
#      detail.
#
# Test verification (sandbox):
#   daemon_plugin_grants + plugin_grants + audit_chain:
#     87 passed
#   Batch B (40 unit files): 954 passed (unchanged delta —
#     8 new daemon tests offset by no other regressions)
#   Zero B238-caused failures.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: additive — new optional field + new route +
#   new event_data subfield. Zero existing client breakage:
#   pre-B238 callers that omit tool_name keep the original
#   plugin-level semantic exactly.
# Per CLAUDE.md Hippocratic gate: no removals; the DELETE
#   refactor extracts shared logic into _do_revoke without
#   changing the existing plugin-level route's behavior.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/plugin_grants.py \
        src/forest_soul_forge/core/audit_chain.py \
        tests/unit/test_daemon_plugin_grants.py \
        docs/decisions/ADR-0053-per-tool-plugin-grants.md \
        dev-tools/commit-bursts/commit-burst238-adr0053-t3-http-api.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(daemon): ADR-0053 T3 per-tool plugin grants API (B238)

Burst 238. ADR-0053 T3 — HTTP API for per-tool plugin grants.

Surface:
  POST /agents/{id}/plugin-grants body gains optional tool_name
    (null = plugin-level, ADR-0043 default; string = per-tool).
  DELETE /agents/{id}/plugin-grants/{plugin} unchanged shape;
    targets only the plugin-level row.
  DELETE /agents/{id}/plugin-grants/{plugin}/tools/{tool} NEW
    per-tool revoke route. Targets the exact triple. Returns
    404 if no per-tool grant exists at the triple; does NOT
    fall back to plugin-level — that's the T4 resolver's job
    at dispatch, not at the revoke endpoint.
  GET /agents/{id}/plugin-grants response rows gain tool_name.

Per ADR-0053 D4 the agent_plugin_granted and agent_plugin_revoked
event_data carry optional tool_name (null for plugin-level,
string for per-tool); event_type stays the same so chronological
queries cover both grant shapes.

DELETE refactored into _do_revoke helper used by both routes —
shared pre-check + audit-emission + revoke flow.

Side cleanup: registered agent_plugin_revoked in
KNOWN_EVENT_TYPES (pre-existing gap that warned at every chain
verification).

Tests: 8 new endpoint tests for the per-tool path; 13 pre-
existing daemon-side tests stay green proving backward compat.

Test results (sandbox):
  daemon_plugin_grants + plugin_grants + audit_chain: 87 passed
  Batch B (40 unit files): 954 passed
  Zero B238-caused failures.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: additive — new optional field + new route.
Per CLAUDE.md Hippocratic gate: no removals."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 238 complete ==="
echo "=== ADR-0053 T3 API live. T4 (dispatcher resolver) queued. ==="
echo "Press any key to close."
read -n 1
