#!/bin/bash
# Burst 407 - run-triune-triage.command registry-direct memory read.
#
# B405's wrapper tried curl GET /agents/<id>/memory?tags=wiring_audit
# but no such endpoint exists in the daemon. Memory is accessed via
# the agent's own tools (memory_recall.v1); there's no operator-side
# memory-read HTTP endpoint outside the consents/consolidation
# sub-routers.
#
# For the scheduled wrapper running on the host, the simplest path
# is reading registry.sqlite directly (read-only sqlite3 + python3 —
# both present on the host). The sandbox-vs-host file-path mapping
# would matter if this script ran in the sandbox, but it runs in
# launchd on the host where data/registry.sqlite is the correct path.
#
# Future enhancement: add a daemon /memory/readable/{instance_id}
# endpoint for operator-side memory inspection. Out of scope here;
# section-15 already flagged a similar gap (no /agents/{id}/memory).
# Track separately.
#
# What this commit adds:
#
# 1. dev-tools/run-triune-triage.command — [2/4] step replaces the
#    curl with a Python heredoc that opens data/registry.sqlite,
#    selects the most-recent wiring_audit-tagged memory_entries row
#    for the WiringSentinel instance_id, and emits the content string.
#    Same data; different access path.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: [2/4] returned empty — scheduled triage cannot run
#     without the upstream sentinel outcome.
#   Prove non-load-bearing: one shell step changes its read path.
#   Prove alternative: add a /memory/readable endpoint — would be
#     correct long-term but requires daemon code + tests + auth
#     review; out of scope for this hotfix.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/run-triune-triage.command \
        dev-tools/commit-bursts/commit-burst407-triune-triage-registry-read.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(triune): registry-direct memory read in triage wrapper (B407)

Burst 407. B405's run-triune-triage.command tried curl GET
/agents/<id>/memory?tags=wiring_audit but no such endpoint
exists. Memory is accessed via the agent's own memory_recall.v1
tool; there's no operator-side memory-read HTTP endpoint outside
the consents/consolidation sub-routers.

Replace [2/4] curl with python3 + sqlite3 against
data/registry.sqlite (read-only). Same data; different access
path. The script runs in launchd on the host where the DB path
resolves cleanly.

Future enhancement queued: /memory/readable/{instance_id}
operator-facing endpoint. Out of scope for this hotfix.

After landing: bash dev-tools/run-triune-triage.command should
fetch the most-recent wiring_audit_outcome + dispatch the
triune skill."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 407 complete - registry-direct memory read ==="
echo "=========================================================="
echo "Next: bash dev-tools/run-triune-triage.command"
echo ""
echo "Press any key to close."
read -n 1 || true
