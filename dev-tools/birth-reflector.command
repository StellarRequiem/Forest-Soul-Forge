#!/bin/bash
# ADR-0087 Phase D — birth Reflector-D2 (reflector role).
#
# Researcher-genre evening-reflection role for D2 Daily Life OS.
# Walks the audit chain via decision_journal_compile.v1, narrates
# via llm_think. Read-only synthesis; never mutates source data.
#
# Posture: GREEN per ADR-0087 Decision 1.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0087 Phase D — Birth Reflector-D2"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load reflector role + decision_journal_compile tool"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing Reflector-D2"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Reflector-D2']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Reflector-D2 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Reflector-D2 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "reflector",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Reflector-D2",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-reflector-d2" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Reflector-D2 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting Reflector-D2's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-reflector-d2-init" \
  -d '{"posture": "green", "reason": "ADR-0087 Decision 1 — reflector read-only evening synthesis is non-acting; forbid_chain_mutation policy enforces read-only stance regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Reflector-D2 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          reflector"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skills:        daily_reflection.v1, decision_journal.v1"
echo "  next steps:    dispatch daily_reflection at the end of"
echo "                 the day for an operator-readable digest."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
