#!/bin/bash
# ADR-0090 Phase B — birth Critic-D10 (critic role).
#
# Guardian-genre adversarial-critique role for D10 Research Lab.
# Consumes the analyst's per-claim decomposition + composes
# counter-arguments (counter-evidence, alternative
# interpretations, missing considerations); writes counter-
# argument attestations tagged for lab_synthesizer pickup.
# Read-only — NEVER overwrites the analyst's verdict
# (forbid_analyst_verdict_overwrite) + NEVER synthesizes
# (lab_synthesizer's lane).
#
# Posture: GREEN per ADR-0090 Decision 1 — counter-argument is
# non-acting; forbid_analyst_verdict_overwrite + forbid_synthesis
# policies enforce role separation at governance layer regardless
# of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0090 Phase B — Birth Critic-D10"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load critic role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing Critic-D10"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Critic-D10']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Critic-D10 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Critic-D10 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "critic",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Critic-D10",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-critic-d10" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Critic-D10 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting Critic-D10's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-critic-d10-init" \
  -d '{"posture": "green", "reason": "ADR-0090 Decision 1 — critic counter-argument is non-acting; forbid_analyst_verdict_overwrite + forbid_synthesis policies enforce role separation regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Critic-D10 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          critic"
echo "  genre:         guardian"
echo "  posture:       green"
echo "  skill:         adversarial_critique.v1"
echo "  next steps:    dispatch adversarial_critique with a"
echo "                 topic_slug to counter the analyst's"
echo "                 decomposition. Lab_Synthesizer-D10 picks"
echo "                 up via counter_argument:* tag."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
