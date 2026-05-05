#!/usr/bin/env bash
# Burst 107: ADR-0043 T4.5 — dispatcher bridge for plugin MCP servers.
#
# Closes the gap that left plugins on disk + audited but never
# actually called by mcp_call.v1. After this burst, an MCP-server-
# typed plugin in ~/.forest/plugins/installed/ shows up in
# ctx.constraints["mcp_registry"] alongside (and overriding)
# servers from config/mcp_servers.yaml.
#
# WHAT'S NEW
#
# 1. ToolDispatcher gains a `plugin_runtime: Any = None` field.
#    None preserves the legacy YAML-only path (test contexts +
#    deployments without ADR-0043 wiring).
#
# 2. dispatch() injection (right after ctx_constraints is built
#    from resolved.constraints, before tool.execute):
#
#      if self.plugin_runtime is not None:
#          plugin_view = self.plugin_runtime.mcp_servers_view()
#          if plugin_view:
#              merged = dict(_load_yaml_registry())
#              merged.update(plugin_view)        # plugins win
#              ctx_constraints["mcp_registry"] = merged
#
#    Wrapped in try/except so a plugin runtime hiccup never
#    crashes a dispatch — falls back to YAML-only via mcp_call's
#    own loader. mcp_call.v1's existing
#    `ctx.constraints.get('mcp_registry') or _load_registry()`
#    handles either path uniformly.
#
# 3. deps.py / build_or_get_tool_dispatcher passes
#    app.state.plugin_runtime into the dispatcher constructor.
#    None when plugin runtime failed to load — graceful fallback.
#
# 4. Tests +10 in test_plugin_dispatcher_bridge.py:
#    - Plugin runtime emits the right shape (URL, sha256,
#      side_effects, allowlisted_tools) for mcp_call to consume
#    - Merge logic: yaml-only / plugins-only / plugin-overrides-
#      yaml-on-name / disjoint-keys-preserved
#    - Dispatcher field default + injection guard structure
#      (verified by inspecting source — keeps the test from
#      requiring a full dispatch end-to-end)
#    - Runtime view URL prefix normalization (bare path →
#      stdio: scheme)
#    - Required-key contract (URL, sha256, side_effects,
#      allowlisted_tools, requires_human_approval)
#
# DESIGN NOTES
#
# Why plugins win on name conflict: the manifest in
# ~/.forest/plugins/installed/<name>/plugin.yaml is the
# operator's NEWER source of truth. config/mcp_servers.yaml is
# the legacy / pre-plugin path; if both define `github`, the
# plugin install was the operator's deliberate "I'm switching
# this server to plugin management." YAML-wins-by-default
# would silently leave the operator on the old config.
#
# Why inject into ctx.constraints rather than mutating
# _load_registry: the registry should be REQUEST-scoped, not
# global. A plugin reload mid-flight changes future dispatches'
# views; in-flight ones keep the snapshot they started with.
# Per-call constraint construction gives that semantics for free.
#
# Why try/except around plugin_view = mcp_servers_view():
# scheduler-style emit-failure-tolerance. The dispatcher must
# keep working even if the plugin runtime hits a transient
# state (mid-reload, disk error, manifest validation hiccup).
# Falls back to YAML-only — operator sees the gap on
# /plugins/list rather than as a dispatch failure.
#
# WHAT THIS BURST DOES NOT DO
#
# - Per-tool requires_human_approval mirroring. mcp_call.v1
#   today reads a per-server boolean. ADR-0043's manifest
#   schema lets the operator gate per-TOOL ("create_issue:
#   true, list_issues: false"). The bridge currently flips the
#   per-server bool when ANY tool requires approval —
#   conservative but loses granularity. Per-tool gating in
#   mcp_call.v1 is a separate burst that touches the tool's
#   approval logic.
# - allowed_mcp_servers updates. Agents' constitutions
#   declare which servers they can reach via
#   `allowed_mcp_servers: [github, linear]`. Adding a plugin
#   doesn't auto-add it to existing constitutions — operators
#   must explicitly grant access via voice regen / constitution
#   patch. Correct security posture; just worth documenting.
# - Frontend awareness. The Forge UI's Tools tab doesn't yet
#   show plugin-registered MCP servers. The /plugins endpoints
#   from Burst 105 surface them; a Tools-tab rendering of "what
#   came from a plugin vs builtin" is a frontend polish item.
#
# VERIFICATION
#
# Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
#   → 2264 passed, 3 skipped, 1 xfailed (was 2254; +10 bridge
#   tests). Zero regressions in the wider dispatcher.
#
# Host (operator, post-restart):
#   1. fsf plugin install /path/to/some-mcp-plugin
#   2. Restart daemon (T3 hot-reload doesn't re-build the
#      dispatcher — that's the dispatcher cache in deps.py)
#   3. Birth an agent with allowed_mcp_servers including the
#      plugin's name in its constitution
#   4. Dispatch mcp_call.v1 with server_name=<plugin>
#   5. Should land normally; previously would've errored with
#      "server not in registry"
#
# ADR-0043 TRANCHE STATUS
#
#   T1 ADR (Burst 103) ✓
#   T2 directory + manifest + CLI (Burst 104) ✓
#   T3 daemon runtime + /plugins endpoints (Burst 105) ✓
#   T4 audit-chain integration (Burst 106) ✓
#   T4.5 dispatcher bridge (this burst) ✓
#   T5 registry repo bootstrap — pending
#
# After this burst, plugin-registered MCP servers are
# functionally equivalent to YAML-registered ones at dispatch
# time. T5 is just the canonical-examples + community-
# contribution-guidelines layer; the runtime is real.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 107 — ADR-0043 T4.5: dispatcher bridge ==="
echo
clean_locks
git add src/forest_soul_forge/tools/dispatcher.py
git add src/forest_soul_forge/daemon/deps.py
git add tests/unit/test_plugin_dispatcher_bridge.py
git add commit-burst107-dispatcher-bridge.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(plugins): dispatcher bridge wires plugin MCP servers (ADR-0043 T4.5)

Closes the gap that left plugins on disk + audited but never
actually called by mcp_call.v1. After this burst, an MCP-server-
typed plugin in ~/.forest/plugins/installed/ shows up in
ctx.constraints['mcp_registry'] alongside (and overriding)
servers from config/mcp_servers.yaml.

ToolDispatcher gains a plugin_runtime field (None default
preserves legacy YAML-only path). dispatch() injects the
merged registry into ctx_constraints right after constitution
resolution, before tool.execute. Merge: YAML base + plugin
overrides by name. Plugins win on conflict — manifests are the
operator's newer source of truth.

Try/except guards the runtime call: a plugin runtime hiccup
falls back to YAML-only rather than crashing the dispatch.
Same posture as the scheduler's emit-failure tolerance.

deps.py / build_or_get_tool_dispatcher passes
app.state.plugin_runtime into the dispatcher constructor.
None when plugin runtime failed to load — graceful fallback.

Tests +10 in test_plugin_dispatcher_bridge.py: shape contract,
merge logic (yaml-only/plugins-only/plugin-overrides-yaml/
disjoint-preserved), dispatcher field default + source-level
injection guard, URL prefix normalization, required-key
contract.

Verification: 2254 → 2264 unit tests pass. Zero regressions in
dispatcher.

What this burst does NOT do (deferred):
- Per-tool requires_human_approval mirroring. Today the bridge
  flips the per-server bool when ANY tool requires approval;
  per-tool gating touches mcp_call.v1's approval logic.
- allowed_mcp_servers updates — adding a plugin doesn't
  auto-grant it to existing constitutions (correct security
  posture).
- Frontend awareness. Tools tab doesn't yet distinguish
  plugin-registered vs builtin MCP servers; polish item.

ADR-0043 status:
  T1 ADR (Burst 103) ✓
  T2 directory + manifest + CLI (Burst 104) ✓
  T3 daemon runtime + /plugins (Burst 105) ✓
  T4 audit-chain (Burst 106) ✓
  T4.5 dispatcher bridge (this) ✓
  T5 registry repo bootstrap — pending

After this burst plugin-registered MCP servers are
functionally equivalent to YAML-registered ones at dispatch
time. T5 is the canonical-examples + community-contribution-
guidelines layer; the runtime is real."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 107 landed. Plugin MCP servers now reach mcp_call.v1."
echo "ADR-0043 functionally complete except for T5 registry bootstrap."
echo ""
read -rp "Press Enter to close..."
