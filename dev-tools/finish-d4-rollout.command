#!/usr/bin/env bash
# Operator-driven finisher for ADR-0077 D4 advanced rollout.
#
# Assumes:
#   1. data/forge/skills/installed/ already contains
#      propose_tests.v1.yaml, safe_migration.v1.yaml,
#      release_check.v1.yaml (copied via the install step or
#      manually from examples/skills/).
#   2. Daemon is running on 127.0.0.1:7423 with B336+ code so
#      the per-role kits land on net-new births.
#
# Does:
#   1. POST /skills/reload so the daemon picks up the three new
#      skill manifests without a full restart.
#   2. Verifies via /skills that all three are loaded.
#   3. Runs birth-d4-advanced.command (idempotent — TestAuthor-D4
#      skipped, the other two get net-new kits from B336).
#   4. Verifies all three agents are alive in /agents.
#
# Stops cleanly on any failure with a clear "this step failed,
# fix and re-run" message.

set -uo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "finish-d4-rollout — install skills + birth remaining agents"
echo "=========================================================="
echo

# ---------------------------------------------------------------------------
# 1. Reload the skill catalog.
# ---------------------------------------------------------------------------
echo "[1/4] POST /skills/reload"
RELOAD=$(curl -s --max-time 10 -X POST "${DAEMON}/skills/reload" \
  -H "X-FSF-Token: $TOKEN" 2>&1)
echo "       response (truncated):"
echo "$RELOAD" | python3 -m json.tool 2>/dev/null | head -20 || echo "$RELOAD" | head -10
echo

# ---------------------------------------------------------------------------
# 2. Verify the three new skills are loaded.
# ---------------------------------------------------------------------------
echo "[2/4] Verify /skills lists the three D4 advanced skills"
CATALOG=$(curl -s --max-time 10 "${DAEMON}/skills" \
  -H "X-FSF-Token: $TOKEN" 2>&1)
MISSING=""
for skill in "propose_tests" "safe_migration" "release_check"; do
  if echo "$CATALOG" | grep -q "\"name\":\"$skill\""; then
    echo "       ✓ $skill"
  else
    echo "       ✗ $skill — NOT in catalog"
    MISSING="$MISSING $skill"
  fi
done
if [ -n "$MISSING" ]; then
  echo
  echo "ERROR: skill(s) missing from catalog after reload:$MISSING"
  echo "Likely cause: manifest parse error. Check the daemon log"
  echo "(.run/daemon.log) for 'skill_catalog' diagnostic entries."
  echo
  echo "Press any key to close."
  read -n 1
  exit 2
fi
echo

# ---------------------------------------------------------------------------
# 3. Run the birth umbrella.
# ---------------------------------------------------------------------------
echo "[3/4] Birthing the three D4 agents (idempotent re-run safe)"
echo "       Forwarding to birth-d4-advanced.command..."
echo
bash ./dev-tools/birth-d4-advanced.command < /dev/null
BIRTH_RC=$?
if [ "$BIRTH_RC" -ne 0 ]; then
  echo
  echo "ERROR: birth-d4-advanced exited rc=$BIRTH_RC. See above"
  echo "for which agent failed."
  echo
  echo "Press any key to close."
  read -n 1
  exit "$BIRTH_RC"
fi
echo

# ---------------------------------------------------------------------------
# 4. Verify all three agents in /agents.
# ---------------------------------------------------------------------------
echo "[4/4] Verify all three D4 advanced agents are alive"
AGENTS=$(curl -s --max-time 10 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>&1)
for name in "TestAuthor-D4" "ReleaseGatekeeper-D4" "MigrationPilot-D4"; do
  INSTANCE=$(echo "$AGENTS" | python3 -c \
    "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='$name']" 2>/dev/null)
  if [ -n "$INSTANCE" ]; then
    echo "       ✓ $name → $INSTANCE"
  else
    echo "       ✗ $name — NOT in /agents"
  fi
done

echo
echo "=========================================================="
echo "D4 advanced rollout finished. Verify in the frontend:"
echo "  Agents tab        — three new entries visible"
echo "  Skills tab        — propose_tests / safe_migration /"
echo "                       release_check listed"
echo "  Pending tab       — empty unless an LLM dispatched at"
echo "                       yellow-posture (TestAuthor or"
echo "                       MigrationPilot)"
echo "=========================================================="
echo
echo "Press any key to close."
read -n 1
