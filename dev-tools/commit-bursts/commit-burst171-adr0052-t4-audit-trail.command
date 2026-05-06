#!/bin/bash
# Burst 171 — ADR-0052 T4 follow-up — audit-trail for resolved
# required_secrets. Closes the audit-emission deferral from B170.
#
# Per the ToolContext docstring's Forest convention:
#
#   "Tools do NOT write audit-chain entries directly. The runtime
#    wraps every dispatch in a tool_invoked entry. Tools that need
#    to expose extra detail surface it via ToolResult.metadata."
#
# So instead of trying to plumb a separate audit_chain handle into
# mcp_call.v1 and emitting four new event types from ADR-0052
# Decision 6, B171 takes the established Forest pattern: surface
# the resolution detail via ToolResult.metadata. The existing
# tool_call_succeeded event hashes that metadata into the chain,
# giving auditors the same forensic visibility ADR-0052 specified
# without inventing parallel event vocabulary.
#
# What ships:
#
#   src/forest_soul_forge/tools/builtin/mcp_call.py:
#     _resolve_required_secrets() now returns a list of audit-
#     trail descriptors — one per successfully-resolved secret.
#     Each descriptor is a dict with exactly three keys:
#
#         secret_name: str   # the name asked for (matches manifest)
#         env_var:     str   # the env var that received the value
#         backend:     str   # store.name (file / keychain / vaultwarden / BYO)
#
#     Critically the descriptor NEVER includes the secret value —
#     only name + env_var + backend. McpCallTool.execute() captures
#     the list and surfaces it via ToolResult.metadata under the
#     key required_secrets_resolved. Both the success path and the
#     MCP-server-said-error path populate this so the operator's
#     forensic query "which plugin used which secrets via which
#     backend?" returns complete results regardless of upstream
#     server behavior.
#
#     Failure modes (backend unreachable, missing secret, .get()
#     raise) all still propagate as McpCallError. The existing
#     tool_call_failed event captures the error_class + message,
#     which contains the backend identifier — so the
#     secret_store_unreachable visibility from ADR-0052 Decision 6
#     is preserved without a dedicated new event type.
#
# What does NOT ship in B171:
#   - The four standalone event types from ADR-0052 Decision 6
#     (secret_put / secret_resolved / secret_delete /
#     secret_store_unreachable). The metadata-on-tool_call pattern
#     above gives operators the forensic data those events would
#     have carried. If a future audit-chain query surface needs
#     finer-grained event-type filtering, a focused tranche can
#     add them; today the existing event vocabulary covers the
#     visibility requirement.
#
# Tests (4 new, +39 total in test_mcp_call_required_secrets):
#
#   - test_empty_list_is_noop now also asserts the returned
#     descriptor list is empty
#   - test_none_list_is_noop same
#   - test_resolved_values_populate_env_vars verifies each
#     resolved secret produces a descriptor with the correct
#     name + env_var + backend, AND verifies the value is NOT
#     in any descriptor (defense-in-depth grep)
#   - test_descriptor_shape_stable: exactly the 3 expected keys.
#     Adding new descriptor fields changes the audit-chain hash
#     for every plugin call that uses required_secrets — this
#     test makes that change deliberate
#   - test_malformed_entries_dont_appear_in_descriptors: skipped
#     entries (None name, missing env_var, non-dict) don't pollute
#     the descriptor list
#
# Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
# changes. The tool_call_succeeded event_data shape gains a new
# optional metadata field, which is additive per ADR-0005 audit-
# chain canonical-form contract (timestamp not in hash; event_data
# is hashed but new keys don't break old replays).
#
# Verification:
#   PYTHONPATH=src pytest tests/unit/test_mcp_call_required_secrets.py
#                                tests/unit/test_plugin_runtime.py
#   -> 39 passed (was 35 in B170; +4 new descriptor coverage tests)
#
# Closes ADR-0052 T4 fully — the operator-facing user story
# "I want to know what secrets the plugin used last time" is
# answerable by jq-ing the audit chain for tool_call_succeeded
# events with required_secrets_resolved metadata.
#
# Remaining ADR-0052 tranches:
#   T3 VaultWardenStore (HTTPS client + config-file loader)
#   T6 Settings UI surface (chat-tab assistant settings panel)

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/builtin/mcp_call.py \
        tests/unit/test_mcp_call_required_secrets.py \
        dev-tools/commit-bursts/commit-burst171-adr0052-t4-audit-trail.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(secrets): ADR-0052 T4 audit trail (B171)

Burst 171. Closes the audit-emission deferral from B170 by
following Forest'\''s established convention from the ToolContext
docstring: tools do not write audit-chain entries directly; they
surface extra detail via ToolResult.metadata, which the existing
tool_call_succeeded event hashes into the chain.

Ships:
- _resolve_required_secrets() returns a list of audit-trail
  descriptors. Each entry is a dict with exactly three keys:
  secret_name, env_var, backend. NEVER the value.
- McpCallTool.execute() surfaces the list via ToolResult.metadata
  under required_secrets_resolved. Both success path and MCP-
  server-error path populate it — operator forensics work either
  way.
- Failure modes (backend unreachable, missing secret) keep
  propagating as McpCallError; the existing tool_call_failed
  event captures the error_class + message, preserving the
  visibility ADR-0052 Decision 6 specified without a dedicated
  new event type.

4 new tests (+39 total): empty list returns empty descriptors;
resolved-values produce correct descriptors and never leak the
value (defense-in-depth grep); descriptor shape stable at the
3 expected keys; malformed entries skipped silently and absent
from the descriptor list.

Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
changes. New optional metadata field is additive per ADR-0005
audit-chain canonical-form contract.

Verification: 39 passed in 0.25s.

Closes ADR-0052 T4 fully. Operator-facing user story which
secrets did this plugin call use is answerable by jq-ing the
audit chain for tool_call_succeeded events with the
required_secrets_resolved metadata.

Remaining ADR-0052 tranches:
- T3 VaultWardenStore (HTTPS client)
- T6 Settings UI surface"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 171 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
