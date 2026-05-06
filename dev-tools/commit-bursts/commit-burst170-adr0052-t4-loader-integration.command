#!/bin/bash
# Burst 170 — ADR-0052 T4 — plugin loader integration. Wires the
# new pluggable secret-store substrate (T1 FileStore + T2
# KeychainStore + T5 CLI) into Forest's actual plugin-launch path
# so plugins declaring required_secrets in plugin.yaml get their
# auth resolved at server-start time and injected as env_vars on
# the spawned subprocess.
#
# Until this commit, the T1/T2 backends + T5 CLI existed but no
# plugin actually consumed them — operators could `fsf secret put
# github_pat` but no MCP server would receive the value. This is
# the unlock.
#
# What ships:
#
#   src/forest_soul_forge/daemon/plugins_runtime.py:
#     mcp_servers_view() now includes a `required_secrets` field
#     in each server's dispatch dict. Shape per RequiredSecret
#     model: {name, env_var, description}. Description preserved
#     from the manifest (used by ADR-0052 T6 install-prompt UI;
#     ignored by the dispatch path). Empty list when the plugin
#     has no operator-managed secrets — every existing example
#     plugin (forest-echo, brave-search, soulux-computer-control,
#     filesystem-reference) keeps required_secrets:[] so byte-for-
#     byte behavior is preserved.
#
#   src/forest_soul_forge/tools/builtin/mcp_call.py:
#     New module-level helper _resolve_required_secrets() factored
#     out of the launch path so it's testable in isolation. The
#     helper:
#       - Reads server_cfg.get("required_secrets") (defaults to [])
#       - On non-empty list, resolves the active backend via
#         resolve_secret_store() — McpCallError on backend
#         unreachable, with FSF_SECRET_STORE pointer
#       - For each entry, calls store.get(name) — McpCallError
#         on backend .get() failure, tied to the specific name
#       - On None return (secret not stored), McpCallError with
#         actionable `fsf secret put <name>` pointer
#       - On success, sets auth_env[entry.env_var] = value
#       - Defense-in-depth: malformed entries (missing name/
#         env_var, non-dict) skipped silently. Manifest schema
#         validates these at load time; this is just belt-and-
#         suspenders for hand-built fixtures.
#     The launch-path call site shrunk to a single function call;
#     the docstring + design rationale lives in the helper.
#
# What does NOT ship in T4 (queued for follow-up):
#   - Audit-chain emission for secret_resolved /
#     secret_store_unreachable event types per ADR-0052 §Decision
#     6. mcp_call.v1 doesn't currently have a clean audit-chain
#     handle. Failures still land via the existing
#     tool_call_failed event with the McpCallError as
#     exception_type, so operators get visibility today —
#     just not at the dedicated event-type level. A separate
#     sub-tranche will plumb the audit handle when we touch
#     mcp_call.v1's audit path next.
#
# Tests:
#
#   tests/unit/test_plugin_runtime.py:
#     +3 tests for the mcp_servers_view required_secrets
#     pass-through:
#       - empty list → empty in dispatch dict
#       - populated list → name + env_var + description preserved
#       - dict shape stable (exactly the 3 expected keys)
#     Existing _write_plugin_dir() helper extended with a
#     required_secrets kwarg.
#
#   tests/unit/test_mcp_call_required_secrets.py (NEW):
#     +9 tests for _resolve_required_secrets():
#       - empty list / None list → no-op (no resolver call,
#         auth_env unchanged)
#       - resolved values populate the declared env_vars
#       - existing auth_env keys (e.g., FSF_MCP_AUTH from the
#         per-call path) survive — helper mutates in place
#       - missing secret → McpCallError pointing at fsf secret put
#       - backend unreachable → McpCallError pointing at
#         FSF_SECRET_STORE
#       - backend .get() raises → McpCallError tied to specific
#         name + backend identifier
#       - malformed entries (None name, missing env_var, non-dict)
#         skipped silently; only well-formed entries fire the
#         resolver
#
# Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
# changes. New userspace helper + dispatch-dict field; no schema
# migration, no new HTTP endpoints, no new audit-chain event
# types (yet — see "What does NOT ship" above).
#
# Verification:
#   PYTHONPATH=src pytest tests/unit/test_mcp_call_required_secrets.py
#                                tests/unit/test_plugin_runtime.py
#                                tests/unit/test_secret_store_conformance.py
#                                tests/unit/test_secret_store_resolver.py
#                                tests/unit/test_keychain_store.py
#                                tests/unit/test_cli_secret_cmd.py
#                                tests/unit/test_posture_gate_step.py
#   -> 125 passed, 4 macOS-only skips
#
# Operator workflow now end-to-end:
#   1. Author a plugin with required_secrets in plugin.yaml
#   2. `fsf plugin install ...` → manifest parsed; required_secrets
#      surfaces in mcp_servers_view
#   3. `fsf secret put <name>` → operator stores values via active
#      backend (Keychain on Mac, file elsewhere)
#   4. Agent grants the plugin via Chat-tab settings panel
#   5. Agent calls a tool → mcp_call.v1 launches the server
#      subprocess with the resolved values in env
#   6. Plugin server reads its env_vars + authenticates upstream
#
# Remaining ADR-0052 tranches:
#   T3 VaultWardenStore (HTTPS client + config-file loader)
#   T6 Settings UI surface (chat-tab assistant settings panel
#      surfaces the active backend + secret list via new daemon
#      endpoints GET /secrets/backend + GET /secrets/names)

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/plugins_runtime.py \
        src/forest_soul_forge/tools/builtin/mcp_call.py \
        tests/unit/test_plugin_runtime.py \
        tests/unit/test_mcp_call_required_secrets.py \
        dev-tools/commit-bursts/commit-burst170-adr0052-t4-loader-integration.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(secrets): ADR-0052 T4 — required_secrets in MCP launch path (B170)

Burst 170. The unlock that makes ADR-0052's pluggable secret-store
substrate (T1 FileStore + T2 KeychainStore + T5 CLI) actually
consumed by plugins. Plugins declaring required_secrets in
plugin.yaml now have those values resolved via the operator's
active backend at server-launch time and injected as env_vars on
the spawned subprocess.

Ships:
- plugins_runtime.mcp_servers_view() includes required_secrets in
  each dispatch dict (shape: {name, env_var, description}).
  Empty list when the plugin has no operator-managed secrets —
  every existing example plugin keeps byte-for-byte behavior.
- mcp_call.py: new module-level helper _resolve_required_secrets()
  factored out of the launch path. Reads required_secrets from
  server_cfg, calls resolve_secret_store(), iterates entries,
  populates auth_env. Failure modes:
  - Backend unreachable → McpCallError with FSF_SECRET_STORE
    pointer
  - Missing secret → McpCallError with `fsf secret put <name>`
    pointer
  - Backend .get() raises → McpCallError tied to specific name
  - Malformed entries skipped silently (defense-in-depth;
    manifest schema validates at load time)

Audit-chain emission for secret_resolved /
secret_store_unreachable event types deferred to a follow-up
sub-tranche pending cleaner audit-chain plumbing into mcp_call.v1.
Failures still land via existing tool_call_failed events with
McpCallError as exception_type.

Tests: +12 across test_plugin_runtime (3 mcp_servers_view
pass-through tests) and test_mcp_call_required_secrets (9
helper tests covering happy/empty/missing/unreachable/malformed
paths).

Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
changes.

Verification: 125 passed, 4 macOS-only skips.

Operator workflow now end-to-end: write plugin with
required_secrets → fsf plugin install → fsf secret put → grant
plugin → agent call → server subprocess gets the env_vars.

Remaining ADR-0052 tranches:
- T3 VaultWardenStore (HTTPS client)
- T6 Settings UI surface"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 170 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
