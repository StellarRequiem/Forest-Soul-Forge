#!/bin/bash
# ADR-0091 Phase A — birth HomeSteward-D5 (home_steward role).
#
# Researcher-genre home-state orchestrator role for D5 Smart
# Home Brain. Reads home_state attestations (operator-supplied
# snapshots OR the forest-home-assistant connector once it
# ships) + composes a state-of-the-home report attestation the
# energy_warden + comfort_optimizer + home_sentinel +
# routine_composer downstream roles consume. Read-only — NEVER
# acts on devices (routine_composer's lane); NEVER raises
# security alerts (home_sentinel's lane); NEVER optimizes
# energy (energy_warden's lane).
#
# Posture: GREEN per ADR-0091 Decision 1 — state-report
# composition is non-acting; forbid_device_action +
# forbid_security_alert + forbid_energy_optimization policies
# enforce role separation at governance layer regardless of
# posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0091 Phase A — Birth HomeSteward-D5"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load home_steward role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing HomeSteward-D5"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='HomeSteward-D5']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      HomeSteward-D5 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing HomeSteward-D5 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "home_steward",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "HomeSteward-D5",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-home-steward-d5" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      HomeSteward-D5 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting HomeSteward-D5's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-home-steward-d5-init" \
  -d '{"posture": "green", "reason": "ADR-0091 Decision 1 — home_steward state-report composition is non-acting; forbid_device_action + forbid_security_alert + forbid_energy_optimization policies enforce role separation regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "HomeSteward-D5 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          home_steward"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         home_orchestration.v1"
echo "  next steps:    dispatch home_orchestration with a window_slug"
echo "                 to compose a state-of-the-home report."
echo "                 HomeSentinel-D5 picks up via the matching"
echo "                 home_state_report:<window_slug> tag."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
