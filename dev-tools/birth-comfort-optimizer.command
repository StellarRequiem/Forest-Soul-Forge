#!/bin/bash
# ADR-0091 Phase B — birth ComfortOptimizer-D5 (comfort_optimizer role).
#
# Researcher-genre comfort-tuning role for D5 Smart Home Brain.
# Dispatches comfort_recommend.v1 over current home_state +
# operator preferences + time-of-day window + composes
# recommendation attestations. Read-only — NEVER actuates devices
# (routine_composer's lane); NEVER classifies energy anomalies
# (energy_warden's lane); NEVER alerts on security
# (home_sentinel's lane).
#
# Posture: GREEN per ADR-0091 Decision 1 — recommendations queue
# for operator review + routine_composer pickup; nothing acts.
# forbid_device_action + forbid_energy_classification +
# forbid_security_alert policies enforce role separation at
# governance layer regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0091 Phase B — Birth ComfortOptimizer-D5"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load comfort_optimizer role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing ComfortOptimizer-D5"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='ComfortOptimizer-D5']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      ComfortOptimizer-D5 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing ComfortOptimizer-D5 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "comfort_optimizer",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "ComfortOptimizer-D5",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-comfort-optimizer-d5" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      ComfortOptimizer-D5 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting ComfortOptimizer-D5's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-comfort-optimizer-d5-init" \
  -d '{"posture": "green", "reason": "ADR-0091 Decision 1 — comfort_optimizer recommendations are non-acting attestations; forbid_device_action + forbid_energy_classification + forbid_security_alert policies enforce role separation regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "ComfortOptimizer-D5 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          comfort_optimizer"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         comfort_tuning.v1"
echo "  next steps:    dispatch comfort_tuning with a window_slug +"
echo "                 time_of_day + areas (per-area current_temp +"
echo "                 current_brightness + current_scene) to compose"
echo "                 recommendations. The attestation tags"
echo "                 comfort_recommendation:<slug> for downstream"
echo "                 routine_composer pickup."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
