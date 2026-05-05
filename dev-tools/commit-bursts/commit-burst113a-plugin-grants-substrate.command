#!/bin/bash
# Burst 113a — ADR-0043 follow-up #2 (substrate layer): plugin
# grant table + dispatcher integration.
#
# Originally Burst 113 was scoped as registry+dispatcher+HTTP+CLI in
# one commit. Split for tighter audit: 113a is the substrate
# (table + dispatcher), 113b will ship the operator surface (HTTP +
# CLI + audit events).
#
# What this fixes:
#   1. The constitution-side allowed_mcp_servers field was documented
#      in mcp_call.v1's docstring but nothing populated
#      ctx.constraints["allowed_mcp_servers"]. mcp_call.v1's
#      allowlist check therefore always saw an empty tuple and
#      refused every server. Pre-existing gap.
#   2. There was no way to grant an MCP plugin to an agent
#      post-birth without rebirthing — the constitution_hash is
#      immutable per agent (CLAUDE.md architectural invariant).
#
# What ships:
#
#   schema v13 → v14:
#     - new agent_plugin_grants table: composite PK (instance_id,
#       plugin_name), trust_tier with CHECK constraint
#       (green/yellow/red), granted_at_seq + revoked_at_seq for
#       audit-chain references, granted_by/revoked_by/reason for
#       operator metadata, FK to agents.instance_id with ON DELETE
#       CASCADE.
#     - idx_plugin_grants_active partial index covering active rows
#       (revoked_at_seq IS NULL).
#     - test assertions across test_registry.py + test_daemon_readonly.py
#       updated from == 13 to == 14.
#
#   src/forest_soul_forge/registry/tables/plugin_grants.py:
#     - new PluginGrantsTable accessor + PluginGrant dataclass.
#     - grant() with INSERT OR REPLACE — re-grant after revoke
#       overwrites the prior row (clean active state).
#     - revoke() flips revoked_at_seq + revoked_at + revoked_by;
#       row stays for historical audit.
#     - list_active(), list_all(), get_active(),
#       active_plugin_names() (the cheap set the dispatcher needs).
#     - trust_tier validation at the Python layer so failures
#       surface before reaching SQLite's CHECK.
#
#   Registry façade (registry/registry.py):
#     - self.plugin_grants accessor exposed alongside self.secrets,
#       self.conversations, etc. Same connection-bound pattern.
#
#   ToolDispatcher integration (tools/dispatcher.py):
#     - new plugin_grants field on ToolDispatcher.
#     - new _load_constitution_mcp_allowlist() helper — reads the
#       constitution top-level allowed_mcp_servers field (was the
#       documented-but-unwired gap closure).
#     - dispatch() execute leg unions constitution allowlist with
#       active grants → ctx.constraints["allowed_mcp_servers"].
#       mcp_call.v1's existing check now sees the union without
#       modification. Errors in the grants table fall back to
#       constitution-only (defensive — never breaks dispatch).
#
#   Daemon wiring (daemon/deps.py):
#     - build_or_get_tool_dispatcher passes
#       fsf_registry.plugin_grants to the dispatcher. None when no
#       registry is wired (test contexts) — dispatcher falls back
#       to constitution-only.
#
# Verification:
#   - tests/unit/test_plugin_grants.py — 19 new tests covering
#     schema bump (3), table semantics (10), constitution helper (5),
#     and dispatcher's union behavior implicitly via the helper +
#     existing test_plugin_dispatcher_bridge integration.
#   - Full unit suite: 2,303 → 2,322 passing (+19, zero regressions).
#
# Outstanding for Burst 113b:
#   - HTTP endpoints: POST /agents/{id}/plugin-grants,
#     DELETE /agents/{id}/plugin-grants/{plugin_name},
#     GET /agents/{id}/plugin-grants — gated by require_writes_enabled +
#     require_api_token, audit-emitting.
#   - 2 new audit event types: agent_plugin_granted,
#     agent_plugin_revoked.
#   - fsf plugin grant/revoke/list CLI subcommand.
#   - Endpoint smoke tests + CLI tests.
#
# Trust-tier field is forward-compatible storage for ADR-0045
# (Agent Posture / Trust-Light System, queued next). Burst 113a
# records the value but the dispatcher only treats it as
# informational — gating still flows through the existing per-tool
# requires_human_approval path. ADR-0045's PostureGateStep will
# start consulting it once filed.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/registry/schema.py
git add src/forest_soul_forge/registry/tables/plugin_grants.py
git add src/forest_soul_forge/registry/registry.py
git add src/forest_soul_forge/tools/dispatcher.py
git add src/forest_soul_forge/daemon/deps.py
git add tests/unit/test_plugin_grants.py
git add tests/unit/test_registry.py
git add tests/unit/test_daemon_readonly.py

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugins): plugin grant substrate + constitution allowlist gap closure (ADR-0043 fu#2 substrate)

Burst 113a — substrate layer of follow-up #2. Originally scoped as a
single Burst 113 (table + dispatcher + HTTP + CLI); split for tighter
audit. 113a is the substrate; 113b will ship the operator surface
(HTTP endpoints + CLI subcommand + audit events).

Two problems closed:

1. Constitution allowlist gap: mcp_call.v1 read
   ctx.constraints[\"allowed_mcp_servers\"] but nothing populated it.
   The constitution top-level field was documented and never wired
   into dispatch — every server was effectively refused.

2. No post-birth plugin grant: constitution_hash is immutable per
   agent (CLAUDE.md invariant), so granting a new MCP server to an
   existing agent required rebirthing. Now operators can issue
   grants that AUGMENT (not mutate) the constitution.

What ships:

- Schema v13 → v14: agent_plugin_grants table with composite PK
  (instance_id, plugin_name), trust_tier CHECK constraint
  (green/yellow/red), granted/revoked seq references for audit
  links, FK cascade to agents. idx_plugin_grants_active partial
  index covers the cheap active-rows lookup.

- registry/tables/plugin_grants.py: PluginGrantsTable + PluginGrant
  dataclass. grant/revoke with INSERT OR REPLACE re-grant semantics.
  active_plugin_names() returns the cheap set the dispatcher needs.

- Registry.plugin_grants accessor exposed alongside secrets/
  conversations/etc.

- ToolDispatcher gains a plugin_grants field. _load_constitution_mcp_allowlist
  helper reads the top-level constitution field (was documented in
  mcp_call.py for over a year but nothing populated it — gap
  closure). dispatch() execute leg unions constitution allowlist
  with active grants and injects as ctx.constraints[\"allowed_mcp_servers\"].
  Defensive — grants table errors fall back to constitution-only.

- daemon/deps.py: build_or_get_tool_dispatcher passes
  fsf_registry.plugin_grants through to the dispatcher. None when
  registry isn't wired (test contexts).

Verification:
- tests/unit/test_plugin_grants.py — 19 new tests:
  - Schema (3): version stamp, table presence, partial index presence,
    DB-layer CHECK constraint.
  - Table semantics (10): grant/list/revoke/regrant cycle, isolation
    per agent, FK cascade on agent delete, get_active behavior,
    invalid trust_tier rejection at Python + SQL layers.
  - Constitution helper (5): present field parsed, missing field /
    missing file / corrupt YAML / mixed-type entries all return
    empty-tuple defensively.
- Full suite: 2,303 → 2,322 (+19, zero regressions).

trust_tier is forward-compatible storage for ADR-0045 (Agent
Posture / Trust-Light System, queued next). Burst 113a records the
value but the dispatcher only treats it as informational — gating
still flows through the existing per-tool requires_human_approval
path. ADR-0045's PostureGateStep will start consulting it.

Outstanding for Burst 113b:
- HTTP: POST/DELETE/GET /agents/{id}/plugin-grants endpoints
- CLI: fsf plugin grant/revoke/list subcommands
- 2 new audit event types: agent_plugin_granted, agent_plugin_revoked
- Endpoint and CLI tests"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 113a commit + push complete ==="
echo "Press any key to close this window."
read -n 1
