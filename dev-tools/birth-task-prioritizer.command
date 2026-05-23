#!/bin/bash
# ADR-0087 Phase C — birth TaskPrioritizer-D2 (task_prioritizer role).
#
# Researcher-genre ranking role for D2 Daily Life OS. Reads
# operator-provided task lists + operator_profile.areas_of_focus,
# ranks deterministically via task_rank.v1, narrates via llm_think.
# NEVER mutates the task store — ranking only.
#
# Posture: GREEN per ADR-0087 Decision 1 — read-only ranking is
# non-acting; forbid_task_store_mutation policy enforces the
# ranking-only stance at governance layer regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0087 Phase C — Birth TaskPrioritizer-D2"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load task_prioritizer role + task_rank tool"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing TaskPrioritizer-D2"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='TaskPrioritizer-D2']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      TaskPrioritizer-D2 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing TaskPrioritizer-D2 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "task_prioritizer",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "TaskPrioritizer-D2",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-task-prioritizer-d2" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      TaskPrioritizer-D2 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting TaskPrioritizer-D2's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-task-prioritizer-d2-init" \
  -d '{"posture": "green", "reason": "ADR-0087 Decision 1 — task_prioritizer read-only ranking is non-acting; forbid_task_store_mutation policy enforces ranking-only stance regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "TaskPrioritizer-D2 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          task_prioritizer"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         task_prioritization.v1"
echo "  next steps:    dispatch task_prioritization with an"
echo "                 operator-provided task list to get a"
echo "                 deterministic ranked digest + narrative."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
