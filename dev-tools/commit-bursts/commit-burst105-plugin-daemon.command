#!/usr/bin/env bash
# Burst 105: ADR-0043 T3 — daemon hot-reload + /plugins HTTP endpoints.
#
# Wires the PluginRepository (Burst 104 T2) into the running daemon.
# Operator can now hit /plugins endpoints, reload the plugin set
# without restart, and see the diff structurally.
#
# WHAT'S NEW
#
# 1. src/forest_soul_forge/daemon/plugins_runtime.py — daemon-side
#    plugin runtime:
#    - PluginRuntime — long-lived in-process view of installed +
#      disabled plugins. One per daemon lifespan. Mutating ops
#      grab app.state.write_lock at the route level (single-writer
#      SQLite discipline extends to plugin filesystem mutations).
#    - ReloadResult — structured diff (added / removed / updated
#      / errors) returned from reload(). Operators acting on the
#      response see exactly what changed.
#    - mcp_servers_view() — converts type=mcp_server manifests
#      into the dict shape mcp_call.v1 already consumes (the same
#      shape config/mcp_servers.yaml produces). T3 ships the
#      conversion + introspection; T4 wires the result into the
#      live dispatch context. Tested for: namespace prefix
#      stripping, requires_human_approval flip-on-any-true, URL
#      shaping (stdio: prefix), exclusion of non-mcp_server types,
#      pass-through of unconventional capabilities.
#    - build_plugin_runtime() — lifespan helper; constructs +
#      performs initial reload.
#
# 2. src/forest_soul_forge/daemon/routers/plugins.py — HTTP surface:
#    - GET /plugins — list active + disabled with full manifests
#      AND the mcp_servers_view bridge dict at the top level
#    - GET /plugins/{name} — single plugin's serialized manifest
#    - POST /plugins/reload (writes + token gated) — re-walk
#      installed/, returns the diff
#    - POST /plugins/{name}/enable (gated) — disabled/<n>/ →
#      installed/<n>/, refresh runtime
#    - POST /plugins/{name}/disable (gated) — inverse
#    - POST /plugins/{name}/verify (gated) — sha256 re-check
#    All POSTs hold app.state.write_lock for filesystem mutations.
#    404 / 409 / 422 mapped from PluginNotFound /
#    PluginAlreadyInstalled / PluginValidationError.
#
# 3. src/forest_soul_forge/daemon/app.py — lifespan wires the
#    runtime onto app.state.plugin_runtime; falls back to None
#    on construction failure (registry malformed, permissions,
#    etc.) with a startup_diagnostics entry. Router included in
#    the standard list.
#
# 4. tests/unit/test_plugin_runtime.py +17 tests covering:
#    - empty start, build_plugin_runtime initial reload
#    - reload diff semantics (added / removed / updated /
#      version-only / sha-only / disabled-not-active)
#    - get / all / state filtering
#    - enable / disable / verify match + mismatch
#    - mcp_servers_view emission, exclusion of disabled,
#      exclusion of non-mcp types, unconventional capability
#      pass-through
#
# WHAT THIS BURST DOES NOT DO
#
# - Audit-chain emit. The 6 plugin_* events from ADR-0043
#   §"Audit events" land in T4 / Burst 106. Hot-reload + enable
#   + disable + verify execute the operations but don't yet
#   write evidence to the chain. Operators see the operation
#   succeed via HTTP response; the chain doesn't yet record it.
# - Constraint injection into ToolDispatcher. mcp_call.v1's
#   _load_registry() still reads config/mcp_servers.yaml only.
#   T4 (or T4.5) populates ctx.constraints["mcp_registry"] from
#   PluginRuntime.mcp_servers_view() so live dispatches see
#   plugin-registered servers. The bridge function exists +
#   tested; the wiring is one constraint-resolver-step away.
#
# DESIGN NOTES
#
# - Runtime owns its own short snapshot lock (threading.Lock)
#   distinct from app.state.write_lock. Snapshot lock prevents
#   readers from observing dicts mid-update during a reload;
#   write_lock serializes against the wider daemon (audit chain,
#   registry). Both held in the right order (write_lock outside,
#   snapshot inside).
# - List + get endpoints are NOT gated. Same posture as /audit
#   + /healthz: read-only operator introspection doesn't require
#   the api_token gate. Mutating endpoints all gated.
# - Reload diff includes version-only and sha256-only changes
#   as updates. T4 will use this distinction for the audit emit
#   (sha-only update is a binary swap; manifest version bump
#   plus sha change is a normal upgrade).
#
# VERIFICATION
#
# Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
#   → 2245 passed, 3 skipped, 1 xfailed (was 2228; +17 runtime
#   tests). Zero regressions in the wider daemon.
#
# Host (operator, post-restart):
#   curl http://127.0.0.1:7423/plugins | python3 -m json.tool
#   # Expected: count=0 (no plugins installed yet)
#   #           mcp_servers_view={} (nothing to bridge)
#
# After installing a plugin via fsf plugin install + restart:
#   curl http://127.0.0.1:7423/plugins | python3 -m json.tool
#   # Expected: count=1, plugin manifest in plugins[], mcp_servers_view
#   #           contains the bridge entry shaped like config/mcp_servers.yaml
#
# Live hot-reload:
#   fsf plugin install /path/to/another-plugin
#   curl -X POST -H "X-API-Token: \$TOKEN" \\
#     http://127.0.0.1:7423/plugins/reload
#   # Expected: {ok: true, added: ["another-plugin"], ...}

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 105 — ADR-0043 T3: daemon hot-reload + /plugins HTTP ==="
echo
clean_locks
git add src/forest_soul_forge/daemon/plugins_runtime.py
git add src/forest_soul_forge/daemon/routers/plugins.py
git add src/forest_soul_forge/daemon/app.py
git add tests/unit/test_plugin_runtime.py
git add commit-burst105-plugin-daemon.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(plugins): daemon runtime + /plugins HTTP endpoints (ADR-0043 T3)

Wires the PluginRepository from Burst 104 into the running
daemon. Operator can now hit /plugins endpoints, reload without
restart, and see the diff structurally.

daemon/plugins_runtime.py — long-lived in-process view:
- PluginRuntime: thread-safe snapshot of installed + disabled
  plugins; mutating ops gated by app.state.write_lock at the
  route level
- ReloadResult: structured diff (added/removed/updated/errors)
- mcp_servers_view(): converts type=mcp_server manifests to
  the dict shape mcp_call.v1 already consumes — namespace
  prefix stripping, requires_human_approval flip-on-any-true,
  URL shaping. T3 ships the bridge function; T4 wires the
  result into the dispatch context.
- build_plugin_runtime(): lifespan helper

daemon/routers/plugins.py — HTTP surface:
- GET /plugins (list, ungated)
- GET /plugins/{name} (one, ungated)
- POST /plugins/reload (gated by writes + token; under write_lock)
- POST /plugins/{name}/{enable,disable,verify} (gated)
404/409/422 mapped from PluginNotFound/AlreadyInstalled/
ValidationError respectively.

daemon/app.py: lifespan instantiates the runtime on
app.state.plugin_runtime with a startup_diagnostics entry.
Failures fall back to None (route returns 503).

Tests +17 in test_plugin_runtime.py covering:
- empty start, build_plugin_runtime initial reload
- reload diff semantics (added/removed/updated/version-only/
  sha-only/disabled-not-active)
- get/all/state filtering
- enable/disable/verify match + mismatch
- mcp_servers_view emission, exclusion of disabled, exclusion
  of non-mcp types, unconventional capability pass-through

What this burst does NOT do:
- Audit-chain emit. T4 / Burst 106 wires the 6 plugin_*
  events. Today operations execute and respond; the chain
  doesn't yet record them.
- Dispatcher constraint injection. mcp_call.v1 still reads
  config/mcp_servers.yaml only. T4 (or T4.5) populates
  ctx.constraints['mcp_registry'] from
  PluginRuntime.mcp_servers_view() so live dispatches see
  plugin-registered servers.

Verification: 2228 → 2245 unit tests pass. Zero regressions.
Host smoke (post-restart): GET /plugins returns the empty
state; install + reload shows the diff."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 105 landed. /plugins HTTP surface is real."
echo "Restart the daemon to pick up the new endpoints + lifespan integration."
echo "Next: Burst 106 — T4 audit-chain integration (6 plugin_* events) + dispatcher bridge."
echo ""
read -rp "Press Enter to close..."
