#!/bin/bash
# Burst 235 — ADR-0053 T1: schema v17 to v18, per-tool plugin grants.
#
# Adds the optional tool_name column to agent_plugin_grants so an
# operator can issue / revoke grants at per-tool granularity, not
# just per-plugin. NULL tool_name = plugin-level grant (the
# ADR-0043 original semantic); non-NULL = per-tool grant.
#
# The ADR estimated a v15-to-v16 migration; reality is v17-to-v18
# because v16 was consumed by ADR-0054 T1 (B178 memory_procedural
# _shortcuts) and v17 by ADR-0060 T1 (B219 agent_catalog_grants)
# in the intervening time. Documented inline in the ADR status
# block.
#
# The migration is a table rebuild rather than ADD COLUMN because
# SQLite cannot ALTER TABLE to change a PRIMARY KEY in place. The
# old (instance_id, plugin_name) PK becomes (instance_id,
# plugin_name, tool_name). Every existing row migrates as NULL
# tool_name, preserving the operator-visible semantic byte-for-
# byte. No incoming FK references this table; the rebuild is
# contained inside one MIGRATIONS[18] transaction.
#
# Two new indexes:
#   idx_plugin_grants_active        re-created post-rename
#     (DROP TABLE discarded the original)
#   ux_plugin_grants_plugin_level   partial unique on
#     (instance_id, plugin_name) WHERE tool_name IS NULL
#     — enforces at-most-one plugin-level grant per (agent,
#     plugin) since the composite PK cannot enforce that when
#     tool_name is NULL (SQLite treats NULL as distinct).
#
# T2-T6 of ADR-0053 queued for subsequent bursts:
#   T2: PluginGrantsTable.grant/revoke accept tool_name
#   T3: HTTP API accepts tool_name + per-tool DELETE path
#   T4: dispatcher specificity-wins resolution
#   T5: ADR-0048 T4 Advanced disclosure becomes interactive
#   T6: cross-ADR doc updates + safety-guide refresh
#
# Test verification (sandbox):
#   Targeted suite (registry/plugin_grants/posture/governance/
#     constitution): 255 passed
#   Full unit suite across all 114 test files: 2,776 passed,
#     11 skipped (env-gated keychain/vaultwarden), 1 expected
#     xfail (v6 to v7 sandbox-only setup limitation)
#   Zero B235-caused failures.
#
# Integration suite has 10 pre-existing failures, all
# X-FSF-Token-gate or trait-count drift — auth-gate migration
# was B206 unit-only; the integration parallel is a separate
# pending item, not B235-caused.
#
# Per ADR-0001 D2: no identity surface touched (constitution_hash
#   stays immutable; grants augment, never mutate).
# Per ADR-0044 D3: additive — new column nullable + new indexes.
#   v17 daemons reading v18 DB via named-column SELECT silently
#   drop tool_name; no breaking change.
# Per CLAUDE.md Hippocratic gate: no removals; all migrations
#   purely additive at the operator-visible semantic level.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/registry/schema.py \
        tests/unit/test_registry.py \
        tests/unit/test_plugin_grants.py \
        tests/unit/test_procedural_shortcuts.py \
        tests/unit/test_daemon_readonly.py \
        docs/decisions/ADR-0053-per-tool-plugin-grants.md \
        dev-tools/commit-bursts/commit-burst235-adr0053-t1-schema.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(registry): ADR-0053 T1 schema v17 to v18 (B235)

Burst 235. ADR-0053 T1 — per-tool plugin grants substrate.

Extends agent_plugin_grants with an optional tool_name column.
NULL tool_name = plugin-level grant (ADR-0043 original semantic,
byte-for-byte compatible). Non-NULL = per-tool grant covering
only that one tool. T2-T6 queued for subsequent bursts.

Migration shape: SQLite cannot ALTER TABLE to change a PRIMARY
KEY in place, so the v18 migration does a standard table-rebuild
(CREATE new shape, INSERT SELECT, DROP, RENAME) rather than the
simpler ADD COLUMN the ADR draft estimated. Old PK
(instance_id, plugin_name) becomes
(instance_id, plugin_name, tool_name). Every existing row
migrates as NULL tool_name, preserving operator-visible
effective-grants semantic. No incoming FK references this table.

Two new indexes:
  idx_plugin_grants_active re-created post-rename
  ux_plugin_grants_plugin_level partial-unique on
    (instance_id, plugin_name) WHERE tool_name IS NULL,
    enforcing one plugin-level grant per (agent, plugin) since
    the composite PK cannot do so when tool_name is NULL.

Version drift documented: ADR estimated v15-to-v16, reality is
v17-to-v18 because v16 was consumed by ADR-0054 T1 (B178) and
v17 by ADR-0060 T1 (B219) between drafting and acceptance.

Test results (sandbox):
  Targeted suite: 255 passed
  Full unit suite (114 files): 2,776 passed, 11 skipped
  Zero B235-caused failures.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: additive column + indexes.
Per CLAUDE.md Hippocratic gate: no removals."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 235 complete ==="
echo "=== ADR-0053 T1 substrate live. T2 (registry surface) queued. ==="
echo "Press any key to close."
read -n 1
