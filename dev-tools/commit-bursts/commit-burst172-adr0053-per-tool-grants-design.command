#!/bin/bash
# Burst 172 — ADR-0053 — per-tool plugin grant substrate (DESIGN).
#
# This commit is design only. Implementation queued in 6 tranches
# (T1-T6, ~4 bursts total). Closes the substrate gap flagged in
# B158 + B165 + B170 — the per-tool reference table in the
# ADR-0048 T4 Advanced disclosure currently ships as read-only
# because Forest's plugin_grants table is plugin-scoped today.
#
# What this ADR locks:
#
# Decision 1 — Schema: add nullable tool_name column to
#   plugin_grants. NULL means plugin-level (the existing semantic);
#   non-NULL narrows to a specific tool. Schema migration v15 to
#   v16, additive only — every existing row stays valid.
#
# Decision 2 — API: POST /agents/.../plugin-grants accepts an
#   optional tool_name. New endpoint shape for per-tool revoke:
#   DELETE .../plugin-grants/{plugin}/tools/{tool_name}. GET
#   response includes tool_name per row.
#
# Decision 3 — Dispatcher resolution precedence: per-tool grant
#   wins over plugin-level grant when both exist. Operator
#   semantic: "more specific wins" — matches Forest's existing
#   patterns (constitutional allow_paths, ADR-0033 filesystem
#   grants).
#
# Decision 4 — Audit-chain events stay the same event_type
#   (agent_plugin_granted + _revoked); the new tool_name field is
#   additive in event_data per ADR-0005 canonical-form contract.
#
# Decision 5 — UI: the B165 read-only per-tool reference table
#   in the ADR-0048 T4 Advanced disclosure becomes an interactive
#   toggle grid wired to the new endpoints. The three preset
#   buttons map onto plugin-level + per-tool grant operations
#   (Restricted = revoke all; Specific = per-tool subset; Full =
#   plugin-level grant at elevated tier).
#
# Decision 6 — Migration safety: pre-v16 rows stay valid because
#   the new column is nullable + the dispatcher coalesces NULL to
#   "plugin-level." Audit-chain replay works against v15 events
#   too — absent tool_name field is treated as NULL.
#
# Implementation tranches (4 bursts total):
#   T1 — Schema migration v15 to v16
#   T2 — Registry surface (grant/revoke accept tool param)
#   T3 — HTTP API (new field + endpoint shape)
#   T4 — Dispatcher resolution (specificity-wins lookup)
#   T5 — Frontend UI (B165 read-only table → interactive grid)
#   T6 — Documentation update (ADR-0048 + ADR-0043 +
#        docs/runbooks/computer-control-safety.md)
#
# What this ADR does NOT do:
#   - No change to ADR-0019 governance pipeline structure
#   - No change to constitution schema (per-tool stays a runtime
#     grant concern, not a constitutional one)
#   - No new audit-chain event types
#   - No per-tool trust-tier semantics — trust_tier applies
#     uniformly across the tools a grant covers
#   - Does NOT migrate existing rows to per-tool granularity;
#     pre-v16 rows stay plugin-level until operator explicitly
#     re-issues them via the UI / CLI
#
# Per ADR-0048 Decision 1 + ADR-0044 D3: implementation will be
# userspace-only with one schema migration (additive). The
# kernel/userspace boundary doc considers schema migrations part
# of the userspace contract — additive ones are non-breaking.
#
# This commit is documentation only — no code touched, no tests
# added.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0053-per-tool-plugin-grants.md \
        dev-tools/commit-bursts/commit-burst172-adr0053-per-tool-grants-design.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0053 — per-tool plugin grants design (B172)

Burst 172. Closes the substrate gap flagged in B158 + B165 +
B170. The per-tool reference table in the ADR-0048 T4 Advanced
disclosure currently ships as read-only because Forest plugin
grants are plugin-scoped today; this ADR locks the substrate
extension that makes per-tool toggles functional.

Six decisions:
1. Schema: nullable tool_name column on plugin_grants.
   NULL = plugin-level (existing); non-NULL = per-tool.
   Schema v15 to v16, additive only.
2. API: POST plugin-grants accepts optional tool_name. New
   DELETE path for per-tool revoke. GET surface includes
   tool_name per row.
3. Dispatcher: per-tool grant wins over plugin-level grant
   when both exist. Specificity-wins matches Forest existing
   patterns.
4. Audit-chain: same event_type (agent_plugin_granted /
   _revoked); tool_name is additive event_data per ADR-0005.
5. UI: B165 read-only table becomes interactive toggle grid.
   Three presets map onto plugin-level + per-tool grant ops.
6. Migration safety: pre-v16 rows stay valid; replay path
   coalesces absent tool_name to plugin-level.

Six implementation tranches queued (~4 bursts total): schema
migration, registry surface, HTTP API, dispatcher resolution,
frontend UI, docs update.

This commit is design only. No code touched, no tests added.

Per ADR-0044 D3: implementation will be userspace-only with one
additive schema migration. Migration safety is preserved.

References: ADR-0019, ADR-0043, ADR-0044, ADR-0045, ADR-0048,
ADR-0005."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 172 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
