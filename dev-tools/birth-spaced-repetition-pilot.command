#!/bin/bash
# ADR-0089 Phase D — birth SpacedRepetitionPilot-D9 (spaced_repetition_pilot role).
#
# Actuator-genre review-queue role for D9 Learning Coach. Dispatches
# spaced_repetition_schedule.v1 (SM-2 interval computation +
# filesystem queue write to data/d9/review_queue.jsonl). YELLOW
# posture default; every non-read-only dispatch operator-gated.
# NEVER fires reminders directly — queue → operator pickup OR
# composition with D2's schedule_reminder.v1 is the separation.
#
# Posture: YELLOW per ADR-0089 Decision 3 — every non-read-only
# dispatch queues for operator approval. Even at GREEN, the per-
# call human approval gate on spaced_repetition_schedule.v1
# (filesystem) remains load-bearing.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0089 Phase D — Birth SpacedRepetitionPilot-D9"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load spaced_repetition_pilot role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing SpacedRepetitionPilot-D9"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='SpacedRepetitionPilot-D9']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      SpacedRepetitionPilot-D9 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing SpacedRepetitionPilot-D9 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "spaced_repetition_pilot",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "SpacedRepetitionPilot-D9",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-spaced-repetition-pilot-d9" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      SpacedRepetitionPilot-D9 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting SpacedRepetitionPilot-D9's posture to YELLOW"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-spaced-repetition-pilot-d9-init" \
  -d '{"posture": "yellow", "reason": "ADR-0089 Decision 3 — spaced_repetition_pilot defaults YELLOW; every non-read-only dispatch queues for operator approval. Composes with D2 schedule_reminder.v1 for fire-time delivery (queue → operator pickup separation)."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "SpacedRepetitionPilot-D9 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          spaced_repetition_pilot"
echo "  genre:         actuator"
echo "  posture:       yellow"
echo "  skill:         spaced_repetition.v1"
echo "  next steps:    dispatch spaced_repetition with topic_slug +"
echo "                 operator-supplied quality (0..5) + source_score_id"
echo "                 to compute the next SM-2 interval + queue the"
echo "                 review. Operator picks queue records up to"
echo "                 dispatch D2's schedule_reminder.v1 for notification."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
