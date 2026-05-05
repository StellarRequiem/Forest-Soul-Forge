#!/bin/bash
# Burst 111 — ADR-0043 deferred follow-up #1: per-tool
# requires_human_approval mirroring for plugin-contributed MCP
# servers.
#
# What this fixes:
#   The pre-Burst-111 mcp_servers_view() flattened the manifest's
#   per-tool requires_human_approval map into a single per-server
#   bool via any(...). A plugin like filesystem-reference (read_file
#   ungated, write_file gated) ended up with the per-server bool=True
#   — but mcp_call.v1 doesn't even read that field. Net effect: per-
#   tool approval declarations in plugin manifests were governance
#   theater. Operators saw the values written, but the runtime
#   ignored them and gated everything via side_effects mapping.
#
# What ships:
#   1. mcp_servers_view() emits requires_human_approval_per_tool
#      (full dict, defensive copy) alongside the existing
#      requires_human_approval per-server bool (kept for back-compat).
#   2. New McpPerToolApprovalStep in governance_pipeline.py — fires
#      ONLY for mcp_call.v1, reads the per-tool map from the merged
#      registry, and forces resolved.constraints["requires_human_
#      approval"]=True on a per-tool match. Audit trail records the
#      override via applied_rules entry "mcp_per_tool_approval[server.tool]".
#   3. Dispatcher refactor: merged MCP registry computed ONCE in
#      _build_merged_mcp_registry() before pipeline.run(). The same
#      dict is reused to populate ctx.constraints["mcp_registry"]
#      for the execute leg — single source of truth, no double-merge.
#   4. DispatchContext gets an mcp_registry field that the new step
#      consults. None when plugin_runtime is unwired (test contexts).
#
# Why fold per-tool gating into the existing constraint path rather
# than emit a new PENDING from the new step:
#   - ApprovalGateStep already knows how to elevate based on the
#     resolved constraint and emits the tool_call_pending_approval
#     event with consistent gate_source semantics. Two parallel
#     PENDING-emitting steps would drift.
#   - applied_rules entry distinguishes per-tool override from a
#     vanilla constitution-driven gate, so the audit chain still
#     captures WHICH path triggered.
#
# Verification:
#   - tests/unit/test_mcp_per_tool_approval_step.py — 14 new tests:
#     - 4 runtime-view shape tests (per_tool field present, defensive
#       copy, per-server bool back-compat preserved)
#     - 10 step semantics tests (gated tool elevates, ungated doesn't,
#       no-op for non-mcp-call, no-op for missing registry, no-op for
#       missing per-tool map, no-op for unknown server, no-op for
#       missing args, no-op for None resolved, preserves existing
#       applied_rules, idempotent on already-True constraint)
#   - test_plugin_dispatcher_bridge.py: 2 source-inspection tests
#     updated to point at _build_merged_mcp_registry helper instead
#     of dispatch() body (the merge logic moved).
#   - Full unit suite: 2,289 → 2,303 (+14, zero regressions)
#
# Outstanding ADR-0043 follow-ups still deferred:
#   - allowed_mcp_servers auto-grant flow (Burst 113-114)
#   - Frontend Tools-tab plugin awareness (Burst 112)
#   - plugin_secret_set audit event (Burst 115+, gated on secrets
#     surface)

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/plugins_runtime.py
git add src/forest_soul_forge/tools/governance_pipeline.py
git add src/forest_soul_forge/tools/dispatcher.py
git add tests/unit/test_mcp_per_tool_approval_step.py
git add tests/unit/test_plugin_dispatcher_bridge.py

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugins): per-tool requires_human_approval mirroring (ADR-0043 fu#1)

First of four ADR-0043 deferred follow-ups (Burst 111). Closes the
governance-theater gap where plugin manifests' per-tool
requires_human_approval map was emitted into the merged registry
but never consulted at dispatch time — every mcp_call.v1 either
gated or didn't gate based on side_effects classification alone,
ignoring the per-tool overrides operators wrote into their plugin
manifests.

What ships:

- mcp_servers_view() emits requires_human_approval_per_tool (full
  dict from the manifest, defensive copy) alongside the existing
  per-server requires_human_approval bool (kept for back-compat
  with the YAML registry shape and any caller using it as a coarse
  hint).

- New McpPerToolApprovalStep in governance_pipeline.py. Fires ONLY
  for mcp_call.v1 dispatches. Reads the per-tool map from
  dctx.mcp_registry, and on a per-tool True forces
  resolved.constraints[\"requires_human_approval\"]=True. The
  downstream ApprovalGateStep then elevates uniformly.
  applied_rules picks up an \"mcp_per_tool_approval[server.tool]\"
  entry so the audit trail captures WHICH per-tool entry fired.

- Dispatcher refactor: the merged MCP registry view (YAML base +
  plugin overrides) is computed ONCE in
  _build_merged_mcp_registry() before pipeline.run(). The same
  dict populates dctx.mcp_registry (for the new step) and
  ctx.constraints[\"mcp_registry\"] (for the execute leg). Single
  source of truth, no double-merge. The two source-inspection tests
  in test_plugin_dispatcher_bridge.py were updated to point at the
  helper instead of dispatch() body.

- DispatchContext gets an mcp_registry: dict | None field. None
  when plugin_runtime is unwired (test contexts) — the new step
  short-circuits to GO in that case.

Why fold per-tool gating into the existing constraint path
rather than emit a new PENDING:
  ApprovalGateStep already owns gate_source semantics and the
  tool_call_pending_approval event. Two parallel PENDING-emitting
  steps would drift. The applied_rules breadcrumb distinguishes
  per-tool override from a vanilla constitution-driven gate, so
  forensic queries (\"what triggered this approval ticket?\") still
  see the per-tool signal.

Verification:
- tests/unit/test_mcp_per_tool_approval_step.py — 14 new tests
  covering both runtime-view shape (4) and step semantics (10):
  gated tool elevates, ungated doesn't, no-op for non-mcp-call,
  no-op for missing/empty registry, no-op for YAML-only entry
  lacking the per-tool map, no-op for unknown server, no-op for
  malformed args, no-op for None resolved, preserves existing
  applied_rules, idempotent on already-True constraint.
- Full unit suite: 2,289 → 2,303 passing (+14 net, matches new
  test file). Zero regressions, 33.79s.

Outstanding ADR-0043 follow-ups deferred (per ADR notes):
  - Frontend Tools-tab plugin awareness (Burst 112 next)
  - allowed_mcp_servers auto-grant flow (Burst 113-114, design
    pass needed on auto vs. operator-confirm)
  - plugin_secret_set audit event (Burst 115+, gated on secrets
    surface)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 111 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
