#!/bin/bash
# Burst 248 — ADR-0061 closed end-to-end.
#
# T6 (HTTP mint) + T7 (CLI subcommand) + audit events +
# tests + status flip. Passport substrate is now operator-
# facing on every surface: programmatic API, HTTP, and CLI.
#
# Files:
#
# 1. src/forest_soul_forge/daemon/schemas/agents.py
#    PassportMintRequest + PassportMintResponse. Mint request
#    body: authorized_fingerprints (required, min 1) + optional
#    expires_at + operator_id + reason. Response carries
#    issuer_public_key, the resolved authorized_fingerprints
#    echo, issued_at, expires_at, passport_path, audit seq +
#    timestamp.
#
# 2. src/forest_soul_forge/daemon/schemas/__init__.py
#    Re-export PassportMintRequest + PassportMintResponse.
#
# 3. src/forest_soul_forge/daemon/routers/passport.py (NEW)
#    POST /agents/{instance_id}/passport. Resolves operator
#    master via resolve_operator_keypair, agent.public_key
#    from registry, mints + writes passport.json next to
#    constitution under the write lock, emits
#    agent_passport_minted audit event.
#    404 unknown agent. 409 legacy agent w/o public_key.
#    422 empty fingerprint list. 403 when writes disabled.
#
# 4. src/forest_soul_forge/daemon/app.py
#    Import + include_router(passport_router.router).
#
# 5. src/forest_soul_forge/core/audit_chain.py
#    Add agent_passport_minted + agent_passport_refused to
#    KNOWN_EVENT_TYPES with motivating comments.
#
# 6. src/forest_soul_forge/tools/governance_pipeline.py
#    HardwareQuarantineStep emits agent_passport_refused when
#    quarantine descriptor carries a passport_reason (operator
#    minted a passport but it failed validation). Quarantine
#    message now mentions BOTH /hardware/unbind AND /passport
#    as remediation paths.
#
# 7. src/forest_soul_forge/cli/passport_cmd.py (NEW)
#    fsf passport subparser. Three subcommands:
#      mint <instance_id> -f <fp> [-f ...] [--expires-at] [--operator-id] [--reason]
#      show <instance_id> [--souls-dir]
#      fingerprint  (prints local fp on stdout, source on stderr)
#    Uses urllib + X-FSF-Token header. Falls back to
#    $FSF_API_TOKEN.
#
# 8. src/forest_soul_forge/cli/main.py
#    Register the passport subparser from passport_cmd.
#
# 9. tests/unit/test_daemon_passport.py (NEW)
#    Endpoint tests: happy path file+event, round-trip
#    verification, unauthorized-fp fails verification, re-mint
#    overwrites + emits twice, 404 unknown agent, 422 empty
#    fingerprints, 403 writes-disabled.
#
# 10. tests/unit/test_cli_passport.py (NEW)
#     Argparse wiring + show (existing/missing/malformed
#     passport) + fingerprint subcommand + mint requires-fp.
#
# 11. docs/decisions/ADR-0061-agent-passport.md
#     Status: Closed 2026-05-12. T6 + T7 rows marked DONE
#     B248 with the implemented detail. Audit-events section
#     added.
#
# 12. docs/runbooks/agent-passport.md
#     "Operator workflow (B248 onward)" rewritten with three
#     options: CLI (recommended), HTTP curl, programmatic.
#
# Verification:
#   - All edited files parse cleanly (ast.parse).
#   - audit_chain KNOWN_EVENT_TYPES contains the two new types.
#   - passport_cmd argparse wires under the main parser.
#   - fsf passport show round-trip + missing + malformed return
#     0 / 4 / 7 respectively.
#   - Daemon router imports cleanly + exposes
#     POST /agents/{instance_id}/passport.
#
# Per ADR-0061 D5 status: every tranche shipped.
# Per ADR-0001 D2: identity surface untouched. Passport is an
#   AUTHORIZATION artifact, not an identity artifact.
# Per ADR-0044 D3: additive. Legacy agents (no public_key on
#   file) get a clear 409 rather than a silent failure.
# Per CLAUDE.md Hippocratic gate: K6 default-deny preserved;
#   the new endpoint is the explicit-roaming escape hatch.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/schemas/agents.py \
        src/forest_soul_forge/daemon/schemas/__init__.py \
        src/forest_soul_forge/daemon/routers/passport.py \
        src/forest_soul_forge/daemon/app.py \
        src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/tools/governance_pipeline.py \
        src/forest_soul_forge/cli/passport_cmd.py \
        src/forest_soul_forge/cli/main.py \
        tests/unit/test_daemon_passport.py \
        tests/unit/test_cli_passport.py \
        docs/decisions/ADR-0061-agent-passport.md \
        docs/runbooks/agent-passport.md \
        dev-tools/commit-bursts/commit-burst248-adr0061-close.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0061 closed — passport HTTP + CLI shipped (B248)

Burst 248. ADR-0061 closes end-to-end with T6 (HTTP mint
endpoint) + T7 (fsf passport CLI subcommand) + audit events
+ tests. Every passport surface is now operator-facing:
programmatic API, HTTP, and CLI.

T6 — POST /agents/{instance_id}/passport in routers/passport.py.
Body: authorized_fingerprints (min 1) + optional expires_at +
operator_id + reason. Resolves operator master via
resolve_operator_keypair, agent.public_key from registry, mints
+ writes passport.json next to constitution under write lock,
emits agent_passport_minted. 404 unknown agent, 409 legacy
agent w/o key, 422 empty fingerprints, 403 writes-disabled.

T7 — fsf passport {mint,show,fingerprint} via cli/passport_cmd.py.
mint posts to the HTTP endpoint. show reads passport.json off
disk + pretty-prints (no HTTP). fingerprint prints the local
hardware fingerprint script-friendly (fp on stdout, source on
stderr).

Audit events agent_passport_minted (router emits on success)
and agent_passport_refused (HardwareQuarantineStep emits when
quarantine descriptor surfaces a passport_reason) added to
KNOWN_EVENT_TYPES. Quarantine refusal message now mentions both
/hardware/unbind AND /passport as remediation paths.

Tests: test_daemon_passport (8 endpoint cases incl. round-trip
verification + re-mint + 404 + 422 + 403) + test_cli_passport
(6 cases incl. parser wiring + show + fingerprint).

ADR-0061 status: Closed 2026-05-12. Runbook updated with
CLI/HTTP/programmatic option matrix.

Per ADR-0001 D2: identity surface untouched.
Per ADR-0044 D3: additive — legacy agents get a clear 409.
Per CLAUDE.md Hippocratic gate: K6 default-deny preserved."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 248 complete ==="
echo "=== ADR-0061 closed. Passport HTTP + CLI both shipped. ==="
echo "Press any key to close."
read -n 1
