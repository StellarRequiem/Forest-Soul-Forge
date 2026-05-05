#!/bin/bash
# Burst 113b — ADR-0043 follow-up #2 operator surface. Closes the
# plugin-grants arc opened by Burst 113a. The substrate (table +
# dispatcher integration) shipped in 113a; this burst adds the HTTP
# endpoints + CLI subcommands + audit event types that operators
# actually use.
#
# What ships:
#
#   src/forest_soul_forge/daemon/routers/plugin_grants.py — new
#     router file (~250 LoC):
#     - GET  /agents/{instance_id}/plugin-grants   (ungated; ?history
#            optionally includes revoked rows)
#     - POST /agents/{instance_id}/plugin-grants   (gated by
#            require_writes_enabled + require_api_token)
#     - DELETE /agents/{instance_id}/plugin-grants/{plugin_name}
#            (same gating)
#     POSTs/DELETEs hold app.state.write_lock and emit audit events
#     atomically with the table mutation. Pre-checks for grant
#     existence on revoke so the audit event payload can include
#     prior_trust_tier (forensic queries about WHAT was revoked, not
#     just that it was).
#
#   2 new audit event types (plain strings; chain takes any):
#     - agent_plugin_granted: {instance_id, plugin_name, trust_tier,
#       granted_by, reason}
#     - agent_plugin_revoked: {instance_id, plugin_name,
#       prior_trust_tier, revoked_by, reason}
#     trust_tier on the granted event is the forward-compat hook for
#     ADR-0045 T3 — when posture enforcement lands, queries against
#     the chain can answer "was this agent allowed to call X without
#     gating at time T?" by reading the event payload.
#
#   src/forest_soul_forge/cli/plugin_cmd.py — three new subcommands:
#     - fsf plugin grant <plugin_name> --to <instance_id>
#         [--tier green|yellow|red] [--reason "..."]
#     - fsf plugin revoke <plugin_name> --from <instance_id>
#         [--reason "..."]
#     - fsf plugin grants --for <instance_id> [--history]
#     All hit the daemon HTTP surface (audit chain emit lives there).
#     Uses urllib (matches cli/triune.py + cli/install.py pattern).
#     --daemon-url + --api-token flags + $FSF_API_TOKEN fallback.
#     Exit codes: 0 success, 4 user error (404/422), 7 server error
#     (5xx, network failures).
#
#   src/forest_soul_forge/daemon/app.py — wire the new router
#     alongside plugins_router.
#
# Verification:
#   - tests/unit/test_daemon_plugin_grants.py — 13 new endpoint tests:
#     - POST happy path + audit event shape
#     - POST default trust_tier=yellow
#     - POST 404 unknown agent
#     - POST 422 invalid trust_tier
#     - POST re-issue overwrites
#     - POST 403 when allow_write_endpoints=False (gating proven)
#     - DELETE happy path + prior_trust_tier captured in audit
#     - DELETE 404 no active grant
#     - DELETE 404 unknown agent
#     - GET active-only by default
#     - GET history=true includes revoked
#     - GET 404 unknown agent
#     - GET ungated (works when allow_write_endpoints=False)
#   - Full unit suite: 2,322 → 2,335 passing (+13, zero regressions).
#
# Closes ADR-0043 follow-up #2 end-to-end. The remaining ADR-0043
# follow-up #4 (plugin_secret_set audit event + secrets surface) is
# scheduled for Burst 116+ and gated on a separate secrets-storage
# decision.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/plugin_grants.py
git add src/forest_soul_forge/daemon/app.py
git add src/forest_soul_forge/cli/plugin_cmd.py
git add tests/unit/test_daemon_plugin_grants.py

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugins): plugin grants operator surface — HTTP + CLI + audit events (ADR-0043 fu#2 operator)

Burst 113b. Closes the plugin-grants arc opened by Burst 113a. The
substrate (table + dispatcher integration + constitution allowlist
gap closure) shipped in 113a; this burst adds the HTTP endpoints,
CLI subcommands, and audit event types that operators actually use.

What ships:

- daemon/routers/plugin_grants.py — new router. GET ungated (same
  posture as /audit + /healthz + GET /plugins). POST + DELETE gated
  by require_writes_enabled + require_api_token. Both mutations
  hold app.state.write_lock and emit audit events atomically with
  table mutation. Revoke pre-checks the grant so the audit payload
  includes prior_trust_tier (forensic queries can answer 'what was
  this agent allowed to do at time T').

- 2 new audit event types:
  agent_plugin_granted {instance_id, plugin_name, trust_tier,
                        granted_by, reason}
  agent_plugin_revoked {instance_id, plugin_name, prior_trust_tier,
                        revoked_by, reason}
  The trust_tier field on the granted event is the forward-compat
  hook for ADR-0045 T3 — once posture enforcement lands, querying
  the chain answers 'was this agent allowed to call X ungated at
  time T?' from the event payload.

- cli/plugin_cmd.py — three new subcommands:
    fsf plugin grant <plugin> --to <instance_id> [--tier ...]
    fsf plugin revoke <plugin> --from <instance_id>
    fsf plugin grants --for <instance_id> [--history]
  All hit the daemon HTTP surface (audit chain emit lives daemon-
  side). Uses urllib (matches cli/triune.py + cli/install.py). Exit
  codes: 0 success, 4 user error (404/422), 7 server error / network.

- daemon/app.py — wire plugin_grants_router alongside plugins_router.

Verification:
- tests/unit/test_daemon_plugin_grants.py — 13 new endpoint tests
  covering POST happy + 404 + 422 + 403 (gating) + reissue, DELETE
  happy + 404x2 + prior_trust_tier captured, GET active + history +
  404 + ungated.
- Full suite: 2,322 → 2,335 (+13, zero regressions).

ADR-0043 follow-up #2 is now end-to-end complete. Remaining
ADR-0043 work: follow-up #4 (plugin_secret_set + secrets surface)
deferred to Burst 116+ pending secrets-storage decision."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 113b commit + push complete ==="
echo "Press any key to close this window."
read -n 1
