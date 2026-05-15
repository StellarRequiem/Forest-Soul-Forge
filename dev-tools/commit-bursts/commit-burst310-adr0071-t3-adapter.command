#!/bin/bash
# Burst 310 - ADR-0071 T3: fsf plugin-adapt (MCP wrapper generator).
#
# Port-face path. Operator points at an upstream MCP server (its
# name, transport, tool list), gets a Forest-compatible plugin
# wrapper. Forest's mcp_call.v1 dispatcher bridges each tool at
# runtime - operator never writes Python.
#
# Distinct from `fsf plugin-new`:
#   - plugin-new ships an operator-authored MCP server stub with
#     Python tool modules (B289).
#   - plugin-adapt wraps an existing upstream binary, no Python
#     stubs (this burst).
#
# What ships:
#
# 1. src/forest_soul_forge/cli/plugin_author.py:
#    - add_adapt_subparser: registers `fsf plugin-adapt` with
#      args (name, --upstream-version, --transport [stdio|http],
#      --command for stdio, --url for http, --tool repeatable,
#      --tier, --license, --target, --force).
#    - _run_adapt: validates name/tool-name regex, transport
#      consistency (stdio requires --command, http requires
#      --url), tier choice, target collision. Generates the
#      plugin.yaml + README.
#    - _render_adapt_plugin_yaml: ADR-0043 mcp_server manifest.
#      Capabilities are mcp.<plugin>.<tool> for each upstream
#      tool. requires_human_approval defaults: false for
#      read_only tier, true for higher tiers. entry_point block
#      differs by transport — stdio carries sha256 placeholder
#      (with shasum walkthrough in the README), http carries
#      url and explicit "no checksum, verify endpoint via
#      TLS/token" guidance.
#    - _render_adapt_readme: install procedure per transport,
#      capability list, what-the-wrapper-IS-NOT clarifier
#      (no Python code; doesnt change upstream tool surface),
#      future-tranche note about introspection.
#
# 2. src/forest_soul_forge/cli/main.py:
#    Imports both add_subparser + add_adapt_subparser from
#    plugin_author; registers `fsf plugin-adapt` at top level
#    alongside `fsf plugin-new`.
#
# Tests (test_cli_plugin_author.py - 13 new T3 cases):
#   Happy:
#     - stdio wrapper produces ADR-0043 manifest with capabilities
#       + sha256 placeholder + correct entry_point.type
#     - http wrapper OMITS sha256 (avoids misleading "verified")
#     - read_only tier defaults requires_human_approval to false
#     - higher tier defaults to true
#   Refusals:
#     - bad name (uppercase)
#     - no --tool supplied
#     - bad tool name (camelCase)
#     - stdio without --command
#     - http without --url
#     - existing target dir without --force
#   Polish:
#     - --force overwrites cleanly
#     - subparser registration produces parseable args
#     - stdio README walks through shasum + fsf plugin install
#     - http README warns about endpoint verification (TLS/token)
#
# Sandbox-verified all 8 scenarios end-to-end (happy stdio +
# happy http + 6 refusal paths). Generated manifests parse as
# YAML; capabilities list shape matches ADR-0043.
#
# What's NOT in T3 (queued):
#   T4: plugin author runbook + publishing guide. Ties together
#       plugin-new (B289+B305) + plugin-adapt (this burst) into
#       a single end-to-end operator workflow doc.
#   Future: live introspection of a running upstream's
#       list_tools() to auto-populate --tool args. Operator
#       currently spells the tool list out from the upstream's
#       README.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/cli/plugin_author.py \
        src/forest_soul_forge/cli/main.py \
        tests/unit/test_cli_plugin_author.py \
        dev-tools/commit-bursts/commit-burst310-adr0071-t3-adapter.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugins): ADR-0071 T3 - fsf plugin-adapt MCP wrapper (B310)

Burst 310. Port-face path: operator points at an upstream MCP
server (name + transport + tool list), gets a Forest-compatible
plugin wrapper. Forest's mcp_call.v1 dispatcher bridges each
tool at runtime - operator never writes Python.

Distinct from fsf plugin-new (B289+B305): plugin-new ships an
operator-authored MCP server stub with Python tool modules;
plugin-adapt wraps an existing upstream binary, no Python stubs.

What ships:

  - cli/plugin_author.py: add_adapt_subparser + _run_adapt +
    _render_adapt_plugin_yaml + _render_adapt_readme. The
    generated manifest follows ADR-0043 mcp_server shape:
    capabilities = mcp.<plugin>.<tool> per upstream tool,
    requires_human_approval defaults per tier (false for
    read_only, true for higher), entry_point block branches
    on transport. Stdio carries a sha256 placeholder with a
    shasum walkthrough in the README - install-time verification
    anchor. Http omits sha256 (no checksum makes sense for a
    network endpoint) and the README explicitly warns about
    TLS/token endpoint verification as the operator's responsibility.

  - cli/main.py: registers fsf plugin-adapt at top level
    alongside fsf plugin-new. Both share the plugin_author
    module since they ultimately produce ADR-0043 manifests of
    different shapes.

Tests: test_cli_plugin_author.py - 13 new T3 cases covering
happy stdio + happy http (sha256 included/omitted by transport),
both tier-default approval policies, all 5 validation refusals,
--force overwrite, subparser registration, README content for
each transport.

Sandbox-verified all 8 functional scenarios pre-commit.

ADR-0071 now 3/4: T1 (plugin-new scaffold B289) + T2 (tier
exemplars B305) + T3 (plugin-adapt this burst). T4 (author
runbook + publishing guide) queued."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 310 complete - ADR-0071 T3 plugin-adapt shipped ==="
echo ""
echo "Press any key to close."
read -n 1
