#!/usr/bin/env bash
# verify-d3-phase-a-live.command — one-shot autonomous verification
# of D3 Phase A end-to-end on the host daemon.
#
# Chain:
#   1. birth-d3-phase-a.command (idempotent; mints
#      ForensicArchivist-D3 if absent, patches constitution, sets
#      posture). Includes its own launchctl kickstart so a separate
#      daemon restart step isn't needed.
#   2. live-test-d3-phase-a.command (installs skill, creates a
#      test artifact, dispatches archive_evidence.v1, verifies
#      ATTEST verdict + memory entry + audit chain tail)
#
# All sub-scripts get stdin redirected to /dev/null so their
# trailing `read -n 1 || true` blocks return EOF immediately
# instead of waiting on a key press. The live-test script's
# blocking `read -r _` at the end will also EOF-exit; the log
# file captures everything for the operator to review.
#
# Why force-restart-daemon is NOT called here:
#   force-restart-daemon.command's last line is `exec ./start.command`
#   — start.command runs `uvicorn ...` in the FOREGROUND and tails
#   the daemon log. exec replaces the calling shell, so calling it
#   from a wrapper hangs the wrapper forever (the tail never exits).
#   Discovered in the first iteration of this wrapper. The birth
#   script's launchctl kickstart is sufficient for a normal restart
#   cycle; use force-restart-daemon manually if you need the full
#   port-cleanup sledgehammer.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

echo "=========================================================="
echo "ADR-0078 Phase A — autonomous live verification"
echo "=========================================================="
echo

# ---- 1. birth the agent ---------------------------------------------------
echo "[1/2] Running birth-d3-phase-a umbrella..."
bash "$HERE/birth-d3-phase-a.command" < /dev/null
RC1=$?
if [ "$RC1" -ne 0 ]; then
  echo "ERROR: birth-d3-phase-a exited rc=$RC1. Stopping — cannot"
  echo "       smoke-test a skill against a non-existent agent."
  echo "Press return to close."
  read -r _
  exit "$RC1"
fi
echo "      Sleeping 3s before live-test..."
sleep 3

# ---- 2. autonomous smoke --------------------------------------------------
echo
echo "[2/2] Running live-test-d3-phase-a..."
cd "$REPO_ROOT"
bash "$REPO_ROOT/live-test-d3-phase-a.command" < /dev/null
RC2=$?

echo
echo "=========================================================="
echo "Verification complete."
echo "  birth umbrella rc=$RC1"
echo "  live-test      rc=$RC2"
echo
echo "Read the live-test log for the full summary:"
echo "  $REPO_ROOT/data/test-runs/d3-phase-a-001/run.log"
echo "=========================================================="
echo
echo "Press return to close."
read -r _
