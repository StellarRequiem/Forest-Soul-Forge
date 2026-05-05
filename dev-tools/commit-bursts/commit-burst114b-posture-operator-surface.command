#!/bin/bash
# Burst 114b — ADR-0045 T2: posture operator surface.
#
# HTTP + CLI + audit event emit for the per-agent posture dial.
# Frontend dial deferred to a follow-up — the HTTP + CLI surfaces
# are the load-bearing parts; the dial is a UX wrapper around them.
#
# What ships:
#
#   src/forest_soul_forge/daemon/routers/agent_posture.py — new
#     router (~125 LoC):
#     - GET  /agents/{instance_id}/posture  (ungated)
#     - POST /agents/{instance_id}/posture  (gated by
#            require_writes_enabled + require_api_token)
#     POST holds app.state.write_lock and emits agent_posture_changed
#     atomically with the column update. Pre-reads prior_posture so
#     the audit payload includes it (forensic queries can answer
#     'what trust did this agent have at time T').
#
#   1 new audit event type:
#     agent_posture_changed {instance_id, prior_posture, new_posture,
#                            set_by, reason}
#     The 68th audit event type (per ADR-0045 §"Audit event").
#
#   src/forest_soul_forge/cli/agent_cmd.py — new CLI module (~135 LoC):
#     - fsf agent posture get <instance_id>
#     - fsf agent posture set <instance_id> --tier green|yellow|red
#         [--reason "..."]
#     Both hit the daemon HTTP. Same urllib pattern as plugin_cmd.
#     --daemon-url + --api-token + $FSF_API_TOKEN fallback.
#     Wired into cli/main.py alongside the existing fsf plugin
#     subcommand.
#
#   src/forest_soul_forge/daemon/app.py — wire agent_posture_router
#     alongside plugin_grants_router.
#
# Frontend dial (deferred):
#   The frontend Agents tab needs a three-state dial widget that
#   calls POST /agents/{id}/posture and refreshes the agent list.
#   Smaller scope than this burst — defer to keep 114b tight. Adding
#   it later is a pure UI change (~100 LoC), no backend coupling.
#
# Verification:
#   - tests/unit/test_daemon_agent_posture.py — 8 endpoint tests:
#     - GET default yellow + 404 unknown agent
#     - POST yellow→green + audit event with prior_posture
#     - POST yellow→red
#     - POST idempotent re-set still emits (operator may want to
#       record a re-affirmation)
#     - POST 404 unknown agent + 422 invalid posture + 403 when
#       writes disabled (gating proven)
#   - Full unit suite: 2,350 → 2,358 (+8, zero regressions).
#
# Outstanding:
#   - Frontend dial (deferred; ~100 LoC pure UI, can land anytime).
#   - Burst 115 / ADR-0045 T3: enforce_per_grant=True on
#     PostureGateStep + 3×3 precedence matrix tests.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/agent_posture.py
git add src/forest_soul_forge/daemon/app.py
git add src/forest_soul_forge/cli/agent_cmd.py
git add src/forest_soul_forge/cli/main.py
git add tests/unit/test_daemon_agent_posture.py

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(posture): operator surface — HTTP + CLI + agent_posture_changed audit (ADR-0045 T2)

Burst 114b. Operator-facing dial for the per-agent posture system
(green / yellow / red). Substrate shipped in Burst 114; this burst
adds the HTTP endpoint, CLI subcommand, and the audit event type
that operators actually use to flip the dial.

What ships:

- daemon/routers/agent_posture.py — GET /agents/{id}/posture
  (ungated read) and POST /agents/{id}/posture (gated by
  require_writes_enabled + require_api_token). POST holds
  app.state.write_lock and emits agent_posture_changed atomically
  with the column update. Pre-reads prior_posture so the audit
  payload captures it — forensic queries answer 'what trust did
  this agent have at time T' from the event payload alone.

- 1 new audit event type:
  agent_posture_changed {instance_id, prior_posture, new_posture,
                         set_by, reason}
  The 68th audit event type per ADR-0045 §'Audit event'.

- cli/agent_cmd.py — new fsf agent subcommand:
    fsf agent posture get <instance_id>
    fsf agent posture set <instance_id> --tier green|yellow|red
        [--reason ...]
  Both hit the daemon HTTP; same urllib pattern as plugin_cmd's
  grant/revoke runners. --daemon-url + --api-token + \$FSF_API_TOKEN
  fallback. Wired into cli/main.py.

- daemon/app.py wires agent_posture_router alongside the existing
  plugin_grants_router.

Frontend dial deferred — ~100 LoC pure UI work, no backend
coupling, can land anytime without a schema or audit dependency.

Verification:
- tests/unit/test_daemon_agent_posture.py — 8 endpoint tests:
  GET default yellow + 404 unknown agent. POST yellow→green +
  audit event with prior_posture captured. POST yellow→red. POST
  idempotent re-set still emits (operators may want to record a
  re-affirmation). POST 404 unknown agent + 422 invalid posture +
  403 when writes disabled (gating proven).
- Full suite: 2,350 → 2,358 (+8, zero regressions).

Outstanding:
- Frontend dial (deferred).
- Burst 115 / ADR-0045 T3: PostureGateStep.enforce_per_grant=True
  + per-grant trust_tier override + 3×3 precedence matrix tests.
  trust_tier was forward-compat storage in Burst 113a; T3 turns it
  on."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 114b commit + push complete ==="
echo "Press any key to close this window."
read -n 1
