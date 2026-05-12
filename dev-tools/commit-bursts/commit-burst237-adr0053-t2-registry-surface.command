#!/bin/bash
# Burst 237 — ADR-0053 T2: PluginGrantsTable per-tool surface.
#
# Extends the registry surface to accept an optional tool_name on
# grant / revoke / get_active operations and to expose tool_name
# on PluginGrant rows + list_active queries. Backward-compatible:
# all existing call sites that pass no tool_name keep the
# plugin-level (ADR-0043 original) semantic exactly.
#
# T2 is purely the SQL/dataclass surface. The dispatcher's
# specificity-wins resolver is T4 (queued); the HTTP API is T3
# (queued). Until T4 lands, no consumer of the table consults
# per-tool rows for trust_tier decisions — the dispatcher's
# _load_plugin_grants_view defensively filters to plugin-level
# rows so its flat plugin→tier dict stays unambiguous even if
# the T3 endpoint (when it lands) lets an operator create
# per-tool grants prematurely.
#
# Files touched:
#
# 1. src/forest_soul_forge/registry/tables/plugin_grants.py
#    - PluginGrant dataclass gains tool_name + is_plugin_level
#      + is_per_tool convenience properties.
#    - grant() accepts tool_name keyword (default None = plugin-
#      level). INSERT OR REPLACE on the new triple key.
#    - revoke() accepts tool_name keyword + uses SQLite IS
#      operator for safe NULL/non-NULL comparison.
#    - get_active() accepts tool_name keyword + uses IS.
#    - New list_active_for_plugin(instance_id, plugin_name)
#      returns both row types ordered plugin-level-first for the
#      T4 resolver.
#    - list_active + list_all + active_plugin_names updated:
#      list/list_all SELECT the new column;
#      active_plugin_names switches to SELECT DISTINCT plugin_name
#      so per-tool + plugin-level on the same plugin dedupes.
#    - _row_to_grant rewires column index 2 as tool_name.
#
# 2. src/forest_soul_forge/tools/dispatcher.py
#    - _load_plugin_grants_view filters to is_plugin_level rows.
#      Pre-T4 defensive measure: keeps the flat plugin→tier dict
#      semantic exact even when per-tool grants exist on the
#      same plugin.
#
# 3. tests/unit/test_plugin_grants.py
#    - 8 new tests for the per-tool path:
#        grant_per_tool_creates_distinct_row
#        get_active_distinguishes_plugin_level_from_per_tool
#        revoke_per_tool_leaves_plugin_level_intact
#        revoke_plugin_level_leaves_per_tool_intact
#        list_active_for_plugin_orders_plugin_level_first
#        active_plugin_names_dedupes_when_per_tool_and_plugin_level_coexist
#        per_tool_grant_idempotent_on_redo
#        per_tool_grant_then_revoke_then_regrant_creates_fresh_active
#    - 32 pre-existing plugin-grants tests stay green proving
#      back-compat.
#
# 4. docs/decisions/ADR-0053-per-tool-plugin-grants.md
#    - Status block bumped to "T1 + T2 shipped".
#    - Tranche table marks T2 DONE with full implementation
#      detail so the doc layer matches the code.
#
# Test verification (sandbox):
#   plugin_grants + daemon_plugin_grants + governance_pipeline +
#     daemon_agent_posture + posture_catalog_grant_matrix:
#     128 passed
#   posture_per_grant + posture_gate_step + b3_posture_tools +
#     integration: 96 passed
#   Batch B (40 unit files): 954 passed (+8 over B235 = the 8
#     new per-tool tests)
#   Batch C (34 files) + integration: 764 passed
#   Zero B237-caused failures.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: additive — new fields + new methods + optional
#   kwargs with safe defaults. Zero existing call-site changes
#   required for back-compat.
# Per CLAUDE.md Hippocratic gate: no removals; the dispatcher's
#   filter is a defensive ADDITION, not a behavior change.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/registry/tables/plugin_grants.py \
        src/forest_soul_forge/tools/dispatcher.py \
        tests/unit/test_plugin_grants.py \
        docs/decisions/ADR-0053-per-tool-plugin-grants.md \
        dev-tools/commit-bursts/commit-burst237-adr0053-t2-registry-surface.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(registry): ADR-0053 T2 per-tool plugin grants surface (B237)

Burst 237. ADR-0053 T2 — PluginGrantsTable accepts optional
tool_name on grant / revoke / get_active and exposes it on rows.

PluginGrant dataclass gains tool_name + is_plugin_level +
is_per_tool. grant() and revoke() accept an optional tool_name
keyword (default None = plugin-level, ADR-0043-compatible).
get_active() accepts tool_name to look up the exact triple
(no fallback — T4's job). New list_active_for_plugin returns
both row types ordered plugin-level-first for the T4 resolver.
active_plugin_names switches to SELECT DISTINCT so per-tool +
plugin-level rows on the same plugin yield one name.

Defensive dispatcher fix: _load_plugin_grants_view filters to
is_plugin_level rows so its flat plugin to tier dict stays
unambiguous pre-T4. When T4 lands, that method can be replaced
with a richer (plugin, tool) keyed view.

Tests: 8 new for the per-tool path; 32 pre-existing stay green
proving backward compatibility.

Test results (sandbox):
  Targeted (plugin_grants + governance + posture + integration
    + b3): 224 passed
  Batch B (40 unit files): 954 passed (+8 over B235)
  Batch C + integration: 764 passed
  Zero B237-caused failures.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: additive — new fields + optional kwargs.
Per CLAUDE.md Hippocratic gate: no removals."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 237 complete ==="
echo "=== ADR-0053 T2 surface live. T3 (HTTP API) queued. ==="
echo "Press any key to close."
read -n 1
