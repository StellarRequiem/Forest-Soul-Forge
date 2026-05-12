#!/bin/bash
# Burst 239 — ADR-0053 T4: specificity-wins dispatcher resolver.
#
# The dispatcher now honors per-tool plugin grants end-to-end.
# An operator creating a per-tool grant via the T3 endpoint
# (B238) actually shapes the mcp_call.v1 dispatch decision: the
# per-tool grant's trust_tier overrides the plugin-level grant's
# trust_tier when the dispatched (server, tool) has a per-tool
# grant. Specificity wins, matching ADR-0053 D3.
#
# The existing ADR-0045 posture × per-grant precedence applies
# unchanged: red-dominates across (posture, tier), with the
# yellow + green downgrade exception. Per-tool tiers feed that
# same matrix — they're just sourced from a per-tool grant row
# instead of a plugin-level grant row when one exists at the
# matched triple.
#
# Files touched:
#
# 1. src/forest_soul_forge/tools/governance_pipeline.py
#    - DispatchContext gains plugin_grant_lookup_fn field
#      (Callable[[str, str | None], str | None] | None).
#      The dispatcher closes over instance_id and provides
#      _resolve_plugin_grant_tier as the lookup.
#    - PostureGateStep restructured to prefer the resolver when
#      wired, fall back to the flat plugin_grants_view when not.
#      Both code paths flow through the same posture × tier
#      precedence logic; only the source of the tier changes.
#
# 2. src/forest_soul_forge/tools/dispatcher.py
#    - New _resolve_plugin_grant_tier(instance_id, plugin, tool)
#      walks list_active_for_plugin rows (returned plugin-level-
#      first by the T2 query) and:
#        - returns the per-tool grant's tier if the named tool
#          has its own row (the override)
#        - else returns the plugin-level grant's tier (fallback)
#        - else None
#      Defensive: returns None on any read error or when the
#      plugin_grants table is unwired (test contexts).
#    - DispatchContext construction wires the resolver via a
#      closure that captures instance_id, so the lookup is lazy
#      per dispatch (table read happens only when the step
#      actually consults the grant).
#    - _load_plugin_grants_view docstring updated to note it's
#      now the fallback path for legacy contexts; the resolver
#      is the preferred input.
#
# 3. tests/unit/test_posture_per_grant.py
#    - 6 new PostureGateStep × resolver interaction tests:
#        per_tool_grant_tier_used_when_resolver_returns_one
#        resolver_returns_none_means_no_grant_input
#        per_tool_red_grant_refuses_even_on_green_agent
#        resolver_preferred_over_plugin_grants_view
#        flat_view_used_when_resolver_absent  (backward compat)
#        resolver_called_with_none_tool_when_args_missing_tool
#    - 6 direct resolver tests:
#        returns_none_when_no_grants
#        plugin_level_grant_returned_when_no_per_tool
#        per_tool_grant_overrides_plugin_level
#        per_tool_grant_doesnt_apply_to_different_tool
#        only_per_tool_grants_no_plugin_level
#        resolver_returns_none_when_table_unwired
#    - 28 pre-existing tests stay green proving back-compat for
#      the flat-view code path.
#
# 4. docs/decisions/ADR-0053-per-tool-plugin-grants.md
#    - Status block bumped to "T1+T2+T3+T4 shipped" with the
#      narrative line: "The dispatcher now honors per-tool
#      grants end-to-end."
#    - Tranche table marks T4 DONE with full implementation
#      detail.
#
# Test verification (sandbox):
#   posture_per_grant + governance_pipeline + plugin_grants +
#     daemon_plugin_grants + posture_catalog_grant_matrix +
#     posture_gate_step: 194 passed
#   Batch B (40 unit files): 966 passed (+12 new T4 tests)
#   Integration: 12 passed
#   Zero B239-caused failures.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: additive — new field on DispatchContext +
#   new method on ToolDispatcher + new branch in PostureGateStep.
#   Legacy contexts that don't wire the resolver keep their
#   pre-B239 behavior exactly via the fallback path.
# Per CLAUDE.md Hippocratic gate: no removals; the
#   _load_plugin_grants_view method stays as the legacy fallback
#   path that the docstring now describes correctly.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/governance_pipeline.py \
        src/forest_soul_forge/tools/dispatcher.py \
        tests/unit/test_posture_per_grant.py \
        docs/decisions/ADR-0053-per-tool-plugin-grants.md \
        dev-tools/commit-bursts/commit-burst239-adr0053-t4-resolver.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(dispatcher): ADR-0053 T4 specificity-wins resolver (B239)

Burst 239. ADR-0053 T4 — per-tool plugin grants now shape
dispatch decisions end-to-end.

New ToolDispatcher._resolve_plugin_grant_tier walks
list_active_for_plugin rows and returns the per-tool grant's
trust_tier when the dispatched (server, tool) has its own grant
(the override), else the plugin-level grant's trust_tier (the
fallback), else None. ADR-0053 D3 specificity-wins.

DispatchContext gains plugin_grant_lookup_fn carrying a closure
over instance_id; PostureGateStep prefers it over the flat
plugin_grants_view when wired. The existing ADR-0045 posture x
per-grant precedence applies uniformly — per-tool tiers feed
the same matrix as plugin-level. Pre-B239 contexts that don't
wire the resolver fall back to the flat view (backward compat).

Tests: 12 new (6 via PostureGateStep, 6 direct resolver); 28
pre-existing posture-per-grant tests stay green proving the
fallback path is unchanged.

Test results (sandbox):
  posture_per_grant + governance + plugin_grants + daemon
    + posture_matrix + posture_gate: 194 passed
  Batch B (40 unit files): 966 passed
  Integration: 12 passed
  Zero B239-caused failures.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: additive — new field + new method + new branch.
Per CLAUDE.md Hippocratic gate: no removals."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 239 complete ==="
echo "=== ADR-0053 T4 resolver live. T5 (frontend UI) queued. ==="
echo "Press any key to close."
read -n 1
