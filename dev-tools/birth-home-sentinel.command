#!/bin/bash
# ADR-0091 Phase A — birth HomeSentinel-D5 (home_sentinel role).
#
# Guardian-genre household-security watcher role for D5 Smart
# Home Brain. Reads home_state attestations + composes alert
# memory_writes for anomalous events (unfamiliar front-door
# presence, vacation-mode-state inconsistency, sensor drift).
# Read-only — the alert attestation is the deliverable; gated
# for operator review + d3_local_soc cascade pickup. NEVER acts
# on devices (routine_composer's lane); NEVER mutates home
# state; NEVER recommends optimization (energy_warden +
# comfort_optimizer's lanes).
#
# Posture: GREEN per ADR-0091 Decision 1 — alerts are non-
# acting attestations; forbid_device_action +
# forbid_state_mutation + forbid_optimization policies enforce
# role separation at governance layer regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0091 Phase A — Birth HomeSentinel-D5"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load home_sentinel role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing HomeSentinel-D5"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='HomeSentinel-D5']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      HomeSentinel-D5 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing HomeSentinel-D5 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "home_sentinel",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "HomeSentinel-D5",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-home-sentinel-d5" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      HomeSentinel-D5 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting HomeSentinel-D5's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-home-sentinel-d5-init" \
  -d '{"posture": "green", "reason": "ADR-0091 Decision 1 — home_sentinel alerts are non-acting attestations; forbid_device_action + forbid_state_mutation + forbid_optimization policies enforce role separation regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "HomeSentinel-D5 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          home_sentinel"
echo "  genre:         guardian"
echo "  posture:       green"
echo "  skill:         home_security.v1"
echo "  next steps:    dispatch home_security with a window_slug to"
echo "                 compose an alert set over recent home_state"
echo "                 snapshots. d5→d3 cascade (Phase D) will route"
echo "                 alerts to d3_local_soc incident_response."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
