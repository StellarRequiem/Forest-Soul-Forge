#!/bin/bash
# ADR-0077 T5 — D4 advanced rollout umbrella birth script.
#
# Runs the three D4 advanced births in the order ADR-0077
# recommends:
#
#   1. birth-test-author          (cheapest, no apply gate;
#                                  observe approval queue first)
#   2. birth-release-gatekeeper   (advisory-only; safe early)
#   3. birth-migration-pilot      (most cautious; birth last so
#                                  the operator has already seen
#                                  the queue + approval flow)
#
# Each individual script is itself idempotent — re-runs skip
# the birth POST when the agent already exists. So the umbrella
# is also re-run-safe.
#
# Operator NOT-required between scripts unless a birth fails.
# This is operator-driven (run from Finder); it does NOT
# auto-restart the daemon (use force-restart-daemon.command for
# that — kit changes from B336 require a restart to take
# effect on net-new births).

set -uo pipefail
cd "$(dirname "$0")"

echo "=========================================================="
echo "ADR-0077 T5 — D4 Advanced Rollout (umbrella birth)"
echo "=========================================================="
echo
echo "This will birth 3 agents in sequence:"
echo "  1. TestAuthor-D4         (researcher  / yellow posture)"
echo "  2. ReleaseGatekeeper-D4  (guardian    / green posture)"
echo "  3. MigrationPilot-D4     (guardian    / yellow posture)"
echo
echo "Each script is idempotent — re-runs skip if the agent"
echo "already exists. Press Ctrl-C now to abort, or wait 3s..."
sleep 3

echo
echo "=========================================================="
echo "[1/3] Birthing TestAuthor-D4"
echo "=========================================================="
bash ./birth-test-author.command < /dev/null
RC1=$?
if [ "$RC1" -ne 0 ]; then
  echo
  echo "ERROR: birth-test-author exited rc=$RC1. Stopping umbrella."
  echo "Fix the cause + re-run; the next two agents are not yet"
  echo "birthed."
  echo
  echo "Press any key to close."
  read -n 1
  exit "$RC1"
fi

echo
echo "=========================================================="
echo "[2/3] Birthing ReleaseGatekeeper-D4"
echo "=========================================================="
bash ./birth-release-gatekeeper.command < /dev/null
RC2=$?
if [ "$RC2" -ne 0 ]; then
  echo
  echo "ERROR: birth-release-gatekeeper exited rc=$RC2. Stopping."
  echo "TestAuthor-D4 was birthed; MigrationPilot-D4 is not."
  echo
  echo "Press any key to close."
  read -n 1
  exit "$RC2"
fi

echo
echo "=========================================================="
echo "[3/3] Birthing MigrationPilot-D4"
echo "=========================================================="
bash ./birth-migration-pilot.command < /dev/null
RC3=$?
if [ "$RC3" -ne 0 ]; then
  echo
  echo "ERROR: birth-migration-pilot exited rc=$RC3."
  echo "TestAuthor-D4 + ReleaseGatekeeper-D4 are alive; MigrationPilot-D4 is not."
  echo
  echo "Press any key to close."
  read -n 1
  exit "$RC3"
fi

echo
echo "=========================================================="
echo "D4 advanced rollout COMPLETE — three agents alive."
echo "=========================================================="
echo
echo "Next steps:"
echo "  * Verify in /agents endpoint or the frontend Agents tab."
echo "  * Install the three skills (examples/skills/*.v1.yaml)"
echo "    into data/forge/skills/installed/ via the Skill Forge"
echo "    UI or operator copy."
echo "  * First real dispatch: software_engineer fires"
echo "    test_proposal capability via decompose_intent →"
echo "    routes to TestAuthor-D4 → propose_tests.v1 runs."
echo
echo "Press any key to close."
read -n 1 || true  # EOF-tolerant for non-interactive callers
