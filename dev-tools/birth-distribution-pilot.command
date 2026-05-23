#!/bin/bash
# ADR-0088 Phase D — birth DistributionPilot-D7 (distribution_pilot role).
#
# Actuator-genre distribution role for D7 Content Studio. Queues
# publishes via publish_schedule.v1; NEVER publishes directly —
# queue → forest-publish connector handoff is the load-bearing
# separation. The only acting role in D7.
#
# Posture: YELLOW per ADR-0088 Decision 3 — every publish action
# is operator-gated regardless of posture. YELLOW posture's
# auto-queue is the second discipline on top of the actuator
# genre's per-call approval gate. Operator flips to GREEN only
# after proposal-quality bedded in.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0088 Phase D — Birth DistributionPilot-D7 (YELLOW)"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load distribution_pilot role + publish_schedule tool"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

mkdir -p "$(pwd)/data/d7"

echo
echo "[2/3] Checking for existing DistributionPilot-D7"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='DistributionPilot-D7']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      DistributionPilot-D7 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing DistributionPilot-D7 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "distribution_pilot",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "DistributionPilot-D7",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-distribution-pilot-d7" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      DistributionPilot-D7 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting DistributionPilot-D7's posture to YELLOW"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-distribution-pilot-d7-init" \
  -d '{"posture": "yellow", "reason": "ADR-0088 Decision 3 — distribution_pilot defaults YELLOW. Every publish action is external + per-call operator-gated regardless of posture; YELLOW is the second discipline (auto-queue every non-read-only dispatch). Operator flips to GREEN only after proposal-quality bedded in."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "DistributionPilot-D7 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          distribution_pilot"
echo "  genre:         actuator"
echo "  posture:       yellow (per-call approval + auto-queue)"
echo "  skills:        scheduled_publishing.v1, performance_tracking.v1"
echo "  queue path:    data/d7/publish_queue.jsonl (created on first call)"
echo "  next steps:    1. Dispatch scheduled_publishing on an"
echo "                    editor-approved + format-adapted artifact."
echo "                 2. Approve the queued publish via the operator"
echo "                    approval queue."
echo "                 3. (Future) forest-publish connector picks"
echo "                    the queue record up at fire_at and dispatches"
echo "                    the real publish."
echo "                 4. Weekly: dispatch performance_tracking for"
echo "                    a digest of queued + fired publishes."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
