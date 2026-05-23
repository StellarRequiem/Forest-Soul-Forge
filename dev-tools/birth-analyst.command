#!/bin/bash
# ADR-0090 Phase A — birth Analyst-D10 (analyst role).
#
# Researcher-genre deep-analysis role for D10 Research Lab.
# Consumes gatherer-produced source bundles + composes per-claim
# decompositions (claim → cited support spans → verify_claim
# verdict). Writes decomposition attestations tagged for critic +
# lab_synthesizer pickup. Read-only — NEVER critiques (critic's
# lane); NEVER synthesizes (lab_synthesizer's lane).
#
# Posture: GREEN per ADR-0090 Decision 1 — deep-analysis is non-
# acting; forbid_critique + forbid_synthesis +
# require_verify_claim_per_decomposition policies enforce role
# separation at governance layer regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0090 Phase A — Birth Analyst-D10"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load analyst role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing Analyst-D10"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Analyst-D10']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Analyst-D10 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Analyst-D10 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "analyst",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Analyst-D10",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-analyst-d10" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Analyst-D10 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting Analyst-D10's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-analyst-d10-init" \
  -d '{"posture": "green", "reason": "ADR-0090 Decision 1 — analyst deep-analysis is non-acting; forbid_critique + forbid_synthesis + require_verify_claim_per_decomposition policies enforce role separation regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Analyst-D10 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          analyst"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         deep_analysis.v1"
echo "  next steps:    dispatch deep_analysis with a topic_slug +"
echo "                 primary_claim to compose a per-claim"
echo "                 decomposition. Gatherer-D10's bundle on the"
echo "                 same topic_slug feeds in via memory_recall."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
