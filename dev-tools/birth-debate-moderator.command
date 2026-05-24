#!/bin/bash
# ADR-0090 Phase C — birth Debate_Moderator-D10 (debate_moderator role).
#
# Researcher-genre debate-moderation role for D10 Research Lab.
# Dispatches debate_orchestrate.v1 (deterministic turn-ordering)
# + claim_provenance.v1 (citation-graph walk) to structure multi-
# agent debates. NEVER takes a substantive turn
# (forbid_substantive_contribution) + NEVER critiques (critic's
# lane) + NEVER synthesizes (lab_synthesizer's lane).
#
# Posture: GREEN per ADR-0090 Decision 1 — moderation is non-
# acting; forbid_substantive_contribution + forbid_critique +
# forbid_synthesis policies enforce role separation at governance
# layer regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0090 Phase C — Birth Debate_Moderator-D10"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load debate_moderator role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing Debate_Moderator-D10"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Debate_Moderator-D10']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Debate_Moderator-D10 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Debate_Moderator-D10 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "debate_moderator",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Debate_Moderator-D10",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-debate-moderator-d10" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Debate_Moderator-D10 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting Debate_Moderator-D10's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-debate-moderator-d10-init" \
  -d '{"posture": "green", "reason": "ADR-0090 Decision 1 — debate_moderator orchestration is non-acting; forbid_substantive_contribution + forbid_critique + forbid_synthesis policies enforce role separation regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Debate_Moderator-D10 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          debate_moderator"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skills:        debate_moderation.v1 + hypothesis_testing.v1"
echo "  next steps:    dispatch debate_moderation with a"
echo "                 topic_slug + question + roles to compose"
echo "                 a structured turn order. hypothesis_testing"
echo "                 delegates short-horizon tests to"
echo "                 Experimenter-Smith (ADR-0056)."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
