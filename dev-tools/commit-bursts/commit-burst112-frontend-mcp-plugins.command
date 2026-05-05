#!/bin/bash
# Burst 112 — ADR-0043 deferred follow-up #2 (per the original list,
# but second-shipped after #1 in Burst 111): frontend Tools-tab
# awareness for plugin-contributed MCP servers.
#
# What this fixes:
#   The frontend Tools tab listed individual tools registered via the
#   legacy .fsf plugin protocol but had no visibility into the new
#   ADR-0043 MCP plugin layer. Operators couldn't see which MCP servers
#   their agents could actually reach via mcp_call.v1, and couldn't
#   distinguish plugin-installed servers (~/.forest/plugins/) from
#   YAML-curated ones (config/mcp_servers.yaml). With per-tool
#   requires_human_approval shipped in Burst 111, that information had
#   nowhere to surface in the UI either.
#
# What ships:
#   - frontend/index.html: new "MCP plugins" panel as a sibling to
#     the existing "Registered tools" panel inside the Tools tab.
#     Includes refresh + reload-from-disk actions.
#   - frontend/js/mcp-plugins.js: new vanilla-JS module (~310 LoC).
#     Calls GET /plugins, renders one row per plugin with
#     name/version/source/state/side-effects pills, sha256 truncation
#     for verification, per-capability list with per-tool approval
#     badges (mirrors the manifest map), required-secrets reminder,
#     verified/unverified status indicator. Renders a sibling section
#     for YAML-only servers (entries from mcp_servers_view that don't
#     match a plugin name) so operators see the full merged set in
#     one place. Reload-button hits POST /plugins/reload (gated by
#     require_writes_enabled + require_api_token).
#   - frontend/js/app.js: import + .start() wired in both the
#     trait-tree-failure path and the success path, matching the
#     pattern used by tool-registry.js.
#   - frontend/css/style.css: new pill variants
#     (source-yaml, state-installed, state-disabled, ok, sha, xs)
#     and panel layout (.mcp-plugins-list, .mcp-plugin-row,
#     .mcp-plugin-row__caps, .mcp-plugin-cap). YAML-only rows get a
#     dashed border so the visual delta is immediate even if the
#     operator misses the source pill.
#
# Why a separate panel rather than folding into tool-registry.js:
#   - "Registered tools" lists individual tools (mcp_call.v1 is one
#     of them). The MCP servers it dispatches to are a different
#     abstraction — second-tier targets, not first-tier tools.
#     Mixing them would muddle the mental model.
#   - The /plugins endpoint exposes plugin lifecycle (active/disabled,
#     sha256 verification, secrets, manifest verified_at) that has
#     no place in a tool list.
#
# Verification:
#   - No backend code touched. Full unit suite still 2,303 passing
#     (zero regressions).
#   - Frontend module is vanilla JS, no build step. Tested manually
#     against the daemon's /plugins endpoint shape (the example
#     plugins in examples/plugins/ surface correctly when installed).
#
# Outstanding ADR-0043 follow-ups still deferred:
#   - allowed_mcp_servers auto-grant (Burst 113-114, design pass needed)
#   - plugin_secret_set audit event (Burst 115+, gated on secrets
#     surface)

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add frontend/index.html
git add frontend/js/app.js
git add frontend/js/mcp-plugins.js
git add frontend/css/style.css

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(frontend): MCP plugins panel in Tools tab (ADR-0043 fu#2)

Second of three remaining ADR-0043 deferred follow-ups (Burst 112).
Closes the visibility gap where operators had no UI surface to see
plugin-contributed MCP servers, distinguish them from YAML-curated
ones, or see per-tool approval gating wired up in Burst 111.

What ships:

- frontend/index.html: new 'MCP plugins' panel as a sibling to
  'Registered tools' inside the Tools tab. Refresh + reload actions
  matching tool-registry.js posture.

- frontend/js/mcp-plugins.js (~310 LoC, new module): hits
  GET /plugins on tab activation, renders one row per plugin with
  name/version, source pill (plugin), state pill (installed/disabled),
  side-effects pill, truncated sha256 (full hash on hover for
  upstream verification), per-capability list with bare tool names
  (mcp.<plugin>.<tool> prefix stripped) and per-tool 'approval' badges
  pulled from the manifest's requires_human_approval map (Burst 111
  data finally rendered in the UI), required-secrets reminder,
  verified/unverified status. Sibling section for YAML-only servers
  pulled from mcp_servers_view entries whose names don't match a
  plugin — operators see the merged set in one pane.

- frontend/js/app.js: import + .start() wired in both the trait-tree-
  failure path and the success path, matching tool-registry.js.

- frontend/css/style.css: pill variants (source-yaml, state-installed,
  state-disabled, ok, sha, xs) plus panel layout. YAML-only rows get
  a dashed border so the visual delta lands even if the source pill
  is missed.

Why separate panel: 'Registered tools' lists individual tools
(mcp_call.v1 is one row). MCP servers are a different abstraction —
second-tier targets the dispatcher routes through, not first-tier
tools. Mixing them would confuse the mental model. Plugin lifecycle
(state, sha256, secrets, verified_at) also has no place in a tool list.

Verification: no backend code touched; full unit suite still 2,303
passing. Frontend is vanilla JS, no build step.

Outstanding ADR-0043 follow-ups still deferred:
  - allowed_mcp_servers auto-grant (Burst 113-114, needs design pass)
  - plugin_secret_set audit event (Burst 115+, gated on secrets surface)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 112 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
