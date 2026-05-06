#!/bin/bash
# Burst 148 — T25 security hardening: auto-generate FSF_API_TOKEN by
# default + plug 12 unauth'd write endpoints.
#
# Triggered by 2026-05-05 outside security review. The review flagged
# the optional-token default as the highest-exposure architectural
# hole. While implementing the fix, also surfaced a SECOND-order bug:
# 12 write endpoints across 5 routers were gated only by
# require_writes_enabled (an on/off feature flag) — NOT by
# require_api_token. So even with a token set, those endpoints
# accepted any local request.
#
# Both bugs fixed in this commit. Verified live: write to
# /conversations without X-FSF-Token returns 401; with token returns 201.
#
# What ships:
#
#   src/forest_soul_forge/daemon/config.py
#     - api_token field description rewritten — explains auto-gen
#       behavior + opt-out
#     - new insecure_no_token: bool = False field — explicit opt-out
#       (matches FSF_ENABLE_PRIV_CLIENT shape)
#
#   src/forest_soul_forge/daemon/app.py
#     - lifespan startup: if api_token unset AND not insecure_no_token,
#       generate cryptographically random token (secrets.token_hex(16)
#       = 32 hex chars), append to .env with explanatory comments,
#       mutate live settings.api_token
#     - if .env write fails: log warning + use token in-memory only
#       (regenerates next restart)
#     - if insecure_no_token=true: log loud warning that writes are open
#     - all paths surface via existing startup_diagnostics list
#       (visible in /healthz response)
#     - bug-fix mid-flight: import Path correctly (initial NameError
#       crashed daemon on first load; fixed in same commit)
#
#   src/forest_soul_forge/daemon/routers/conversations.py
#   src/forest_soul_forge/daemon/routers/conversations_admin.py
#   src/forest_soul_forge/daemon/routers/audit.py
#   src/forest_soul_forge/daemon/routers/hardware.py
#   src/forest_soul_forge/daemon/routers/triune.py
#     - 5 router files: add require_api_token to imports, change
#       12 endpoints' dependencies from
#         dependencies=[Depends(require_writes_enabled)]
#       to
#         dependencies=[Depends(require_writes_enabled),
#                       Depends(require_api_token)]
#     - Surfaced via grep audit while diagnosing why verify-b148
#       returned 201 on a no-token write. Auth surface was already
#       inconsistent pre-B148; this completes it.
#     - Affected endpoints (12 total):
#         conversations.py (8): /conversations POST, /{id}/status,
#           /{id}/retention, /{id}/participants, /{id}/turns, DELETE
#           /{id}/participants/{iid}, /{id}/bridge, /{id}/ambient/nudge
#         conversations_admin.py (1): /admin/conversations/sweep_retention
#         audit.py (1): /audit/ceremony
#         hardware.py (1): /agents/{id}/hardware/unbind
#         triune.py (1): /triune/bond
#
#   .env.example
#     - rewrites the auth section to document the new behavior
#     - shows both FSF_API_TOKEN and FSF_INSECURE_NO_TOKEN templates
#
#   verify-b148.command (new at repo root) — focused end-to-end
#     verification. Restart daemon, read .env for auto-generated
#     token, exercise write endpoint without token (expect 401) and
#     with token (expect 201). Cleans up the test conversation.
#
# Verified live 2026-05-05:
#   - daemon restart triggers auto-gen if .env doesn't have token
#   - .env gets new line appended with timestamp + opt-out doc
#   - /conversations POST without X-FSF-Token → 401 ENFORCED
#   - /conversations POST with X-FSF-Token → 201 (full chat path)
#   - test conversation archived for cleanup (also via authed call)
#
# Closes T25. Highest-exposure architectural hole from the security
# review is now closed by default, AND the auth-coverage gap is
# closed as a side effect.
#
# Migration note for operators: existing scripts that POST to write
# endpoints need FSF_API_TOKEN exported. Most have ${FSF_API_TOKEN:-}
# fallback already; operator runs `export $(grep ^FSF_API_TOKEN .env)`
# before scripts. A B149 follow-on can ship a shared env-loader to
# automate. Or operators can pipe through verify-b148.command which
# auto-reads .env.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/config.py \
        src/forest_soul_forge/daemon/app.py \
        src/forest_soul_forge/daemon/routers/conversations.py \
        src/forest_soul_forge/daemon/routers/conversations_admin.py \
        src/forest_soul_forge/daemon/routers/audit.py \
        src/forest_soul_forge/daemon/routers/hardware.py \
        src/forest_soul_forge/daemon/routers/triune.py \
        .env.example \
        verify-b148.command \
        dev-tools/commit-bursts/commit-burst148-api-token-auto-gen.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): T25 — FSF_API_TOKEN auto-generated + 12 endpoints plugged (B148)

Burst 148. Closes T25 from the 2026-05-05 outside security review.

Two bugs fixed:

1. Auto-generate FSF_API_TOKEN by default (closes the highest-exposure
   hole the review flagged). Pre-B148: api_token defaulted to None,
   require_api_token bypassed when None. Any local process could hit
   write endpoints without auth. Now: if api_token unset, daemon
   generates a token (secrets.token_hex(16)), writes to .env with
   explanatory comments, uses for the run. Operators who really want
   no-auth opt out via FSF_INSECURE_NO_TOKEN=true.

2. 12 write endpoints across 5 routers were gated only by
   require_writes_enabled (an on/off feature flag) — NOT by
   require_api_token. Surfaced while diagnosing why verify-b148 got
   201 on a no-token write. Auth surface was already inconsistent;
   this completes it. Endpoints fixed:
   - conversations.py (8): POST /conversations, /{id}/status,
     /{id}/retention, /{id}/participants (POST + DELETE), /{id}/turns,
     /{id}/bridge, /{id}/ambient/nudge
   - conversations_admin.py (1): /admin/conversations/sweep_retention
   - audit.py (1): /audit/ceremony
   - hardware.py (1): /agents/{id}/hardware/unbind
   - triune.py (1): /triune/bond

Ships:
- daemon/config.py: api_token desc rewritten; new insecure_no_token
  field
- daemon/app.py: lifespan auto-gen with .env persistence + import fix
- 5 router files: require_api_token added to imports + dependencies
- .env.example: documents both opt-out paths
- verify-b148.command: end-to-end verify (asserts 401 + 201)

Verified live 2026-05-05:
- daemon auto-generates token on restart, writes to .env
- /conversations POST without X-FSF-Token → 401 ENFORCED
- /conversations POST with X-FSF-Token → 201 (full chat path)
- test conversation archived via authed call

Migration: existing operator scripts need FSF_API_TOKEN exported.
Most have \${FSF_API_TOKEN:-} fallback; operator runs
'export \$(grep ^FSF_API_TOKEN .env)' before scripts. B149 follow-on
can ship a shared env-loader.

Closes T25. Phase 4 (security hardening) item #1 done. Future:
T26 (SBOM), T27 (per-event signatures), T28 (encryption at rest),
T29 (per-tool sandbox)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 148 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
