#!/bin/bash
# ADR-0087 Phase B — birth TimeSteward-D2 (time_steward role).
#
# Actuator-genre scheduling + calendar role. The only acting role
# in D2. YELLOW posture default per ADR-0087 Decision 2 — every
# non-read-only dispatch queues for operator approval until the
# operator explicitly promotes to GREEN.
#
# Even after GREEN, the actuator genre's external ceiling + the
# per-call requires_human_approval gates on schedule_reminder.v1
# (filesystem) + calendar_block.v1 (external) keep every action
# operator-reviewed.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0087 Phase B — Birth TimeSteward-D2"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load time_steward role + new tools"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing TimeSteward-D2"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='TimeSteward-D2']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      TimeSteward-D2 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing TimeSteward-D2 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "time_steward",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "TimeSteward-D2",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-time-steward-d2" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      TimeSteward-D2 born: instance_id=${INSTANCE_ID}"
fi

# Ensure D2 data dirs exist for the ledgers + queues
mkdir -p "$(pwd)/data/d2"

echo
echo "[3/3] TimeSteward-D2 stays at YELLOW posture (ADR-0087 Decision 2 default)"
echo "      No posture flip — operator promotes to GREEN explicitly"
echo "      after proposal-quality bedding-in."

echo
echo "=========================================================="
echo "TimeSteward-D2 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          time_steward"
echo "  genre:         actuator"
echo "  posture:       yellow (default per ADR-0087 D2)"
echo "  skills:        schedule_reminder.v1, calendar_management.v1"
echo "  reminder log:  data/d2/reminders.jsonl"
echo "  calendar queue: data/d2/calendar_queue.jsonl"
echo "  next steps:    dispatch schedule_reminder skill for a"
echo "                 future ISO timestamp; review the queued"
echo "                 entry via the approval queue."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
