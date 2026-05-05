#!/usr/bin/env bash
# Burst 106: ADR-0043 T4 — audit-chain integration for plugin lifecycle.
#
# Wires 5 of the 6 ADR-0043 audit events into PluginRuntime
# methods. After this burst, every operator-driven plugin
# transition (install, enable, disable, uninstall, verification
# failure) lands on the audit chain alongside builtin tool calls.
#
# WHAT'S NEW
#
# 1. PluginRuntime.__init__ takes optional audit_chain. None
#    keeps tests + chain-load-failure paths quiet; presence emits.
#    Mirrors the scheduler's audit-emit policy from Burst 89.
#
# 2. Reload diff emits:
#      plugin_installed   for every name in added (newly active)
#      plugin_uninstalled for every name that vanished from disk
#                         (was active OR disabled, now gone entirely)
#    The diff distinguishes "moved to disabled/" (NOT uninstalled)
#    from "directory removed" (IS uninstalled) — moving to disabled
#    keeps the plugin known to the runtime. plugin_disabled fires
#    from disable() instead.
#
# 3. enable() emits plugin_enabled after the move + reload. The
#    reload's plugin_installed (re-appearance in active set) AND
#    the explicit plugin_enabled (operator action) both fire —
#    they capture different facts.
#
# 4. disable() emits plugin_disabled.
#
# 5. verify() emits plugin_verification_failed on sha256 mismatch.
#    Successful verifies skip emit (periodic polls would otherwise
#    flood the chain with no-ops).
#
# 6. _emit_audit() helper — best-effort; logs + swallows on
#    chain.append failures. Same shape as Scheduler._emit_audit.
#
# 7. build_plugin_runtime() forwards audit_chain to the runtime.
#    Lifespan passes the daemon's chain. Initial reload emits
#    plugin_installed events for every plugin found at startup,
#    giving the chain a clean post-restart baseline.
#
# WHAT THIS BURST DOES NOT DO
#
# - plugin_secret_set event. T2 didn't ship a secrets CLI/HTTP
#   surface (the agent_secrets store from ADR-003X K1 already
#   exists; per-plugin secret operations are deferred to a
#   focused follow-up burst). When that lands, the emit point
#   is one self._emit_audit("plugin_secret_set", ...) call
#   away.
# - Dispatcher constraint injection. mcp_call.v1 still reads
#   config/mcp_servers.yaml only. Bridge function
#   PluginRuntime.mcp_servers_view() exists from Burst 105 +
#   tested; populating ctx.constraints["mcp_registry"] from it
#   touches the dispatcher's constraint resolution path more
#   invasively than fits this burst. Tracked as T4.5.
# - plugin_updated event. Reload diff distinguishes version-only
#   vs sha-only changes; T5 may add a plugin_updated event if
#   operators need binary upgrades on the chain.
#
# DESIGN NOTES
#
# - Audit emits happen OUTSIDE the snapshot lock. chain.append
#   may take longer than a microsecond; we don't want readers
#   blocked on it. The dict swap completes atomically under the
#   snapshot lock, then emits run on the snapshot data we
#   captured before swap.
# - Successful verify is silent by design. A periodic verify
#   loop (T5+ option) would otherwise spam the chain. Operators
#   asking "when did this last verify clean?" can use the chain
#   absence + the last successful dispatch event instead.
# - plugin_uninstalled fires for plugins that vanish from BOTH
#   active and disabled — operator either ran fsf plugin
#   uninstall, or rm -rf'd the directory by hand. Either way
#   the chain captures the "no longer here" state transition.
#
# VERIFICATION
#
# Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
#   → 2254 passed, 3 skipped, 1 xfailed (was 2245; +9 audit
#   tests). Zero regressions in the wider daemon.
#
# Tests cover:
#   - reload-emits-installed for added
#   - reload-emits-uninstalled for removed
#   - reload-does-not-emit-uninstalled-when-only-disabled
#   - enable-emits-plugin-enabled (alongside the reload's
#     plugin_installed)
#   - disable-emits-plugin-disabled
#   - verify-mismatch-emits-plugin_verification_failed
#   - verify-match-does-not-emit (silent on success)
#   - audit-emit-failure-does-not-break-runtime
#   - runtime-without-chain-is-silent-no-op (None handle)
#
# Host (operator, post-restart):
#   curl -X POST -H "X-API-Token: \$TOKEN" \\
#     http://127.0.0.1:7423/plugins/reload
#   curl http://127.0.0.1:7423/audit/tail?n=20 | jq '.events[].event_type'
#   # Expect plugin_installed entries for every plugin currently
#   # in installed/. Subsequent enable/disable/verify-mismatch
#   # operations land on the chain in real time.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 106 — ADR-0043 T4: plugin audit-chain integration ==="
echo
clean_locks
git add src/forest_soul_forge/daemon/plugins_runtime.py
git add src/forest_soul_forge/daemon/app.py
git add tests/unit/test_plugin_runtime.py
git add commit-burst106-plugin-audit.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(plugins): audit-chain integration for plugin lifecycle (ADR-0043 T4)

Wires 5 of the 6 ADR-0043 §'Audit events' into PluginRuntime.
After this burst, every operator-driven plugin transition lands
on the chain alongside builtin tool calls.

Events emitted:
- plugin_installed   — reload diff: name newly in active set
- plugin_uninstalled — reload diff: name vanished from disk
                       entirely (NOT just moved to disabled/)
- plugin_enabled     — explicit operator action via enable()
- plugin_disabled    — explicit operator action via disable()
- plugin_verification_failed — sha256 mismatch detected by
                       verify() (successful verifies stay silent
                       to avoid flooding the chain on periodic polls)

Best-effort emit policy (mirrors scheduler's from Burst 89):
chain.append failures log + swallow; runtime keeps working.
Audit emits happen OUTSIDE the snapshot lock so a slow
chain.append doesn't block concurrent readers.

PluginRuntime.__init__ takes optional audit_chain. None keeps
tests + chain-load-failure paths quiet. build_plugin_runtime()
forwards from lifespan. Initial reload emits plugin_installed
for every plugin found at startup — gives the chain a clean
post-restart baseline of what the daemon thinks is active.

Tests +9 in test_plugin_runtime.py covering each emit path:
- added → plugin_installed
- removed → plugin_uninstalled
- moved to disabled is NOT uninstalled
- enable emits plugin_enabled
- disable emits plugin_disabled
- verify mismatch emits plugin_verification_failed
- verify match is silent
- broken chain doesn't break runtime
- None chain is silent no-op

Verification: 2245 → 2254 unit tests pass. Zero regressions.

What this burst does NOT do (deferred):
- plugin_secret_set event — no secrets surface yet (agent_secrets
  store from ADR-003X K1 exists; per-plugin secrets is a focused
  follow-up). Emit point is one call away when wired.
- Dispatcher constraint injection. mcp_servers_view() bridge
  function exists + tested from Burst 105; populating
  ctx.constraints['mcp_registry'] from it touches the dispatcher
  path more invasively than fits this burst. Tracked as T4.5.
- plugin_updated event for binary upgrades — T5 may add it if
  operators need it.

ADR-0043 status:
  T1 ADR (Burst 103) ✓
  T2 directory + manifest + CLI (Burst 104) ✓
  T3 daemon runtime + /plugins endpoints (Burst 105) ✓
  T4 audit-chain integration (this burst) ✓
  T4.5 dispatcher bridge — pending
  T5 registry repo bootstrap — pending"

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 106 landed. Plugin lifecycle is on the audit chain."
echo "Next: T4.5 (dispatcher bridge — wire mcp_servers_view into ctx.constraints) or"
echo "T5 (registry repo bootstrap)."
echo ""
read -rp "Press Enter to close..."
