#!/bin/bash
# ADR-0092 Phase A — birth RiskAdvisor-D6 (risk_advisor role).
#
# Guardian-genre anti-recommendation arbiter role for D6 Personal
# Finance Guardian. Reads candidate-operator-action attestations
# + composes "this is out-of-pattern" alert attestations the
# operator picks up to inform their decision. Read-only — the
# alert attestation is the deliverable. NEVER blocks the
# operator (operator decides — same discipline as reality_anchor);
# NEVER executes transactions (manifest hard rule); NEVER advises
# on specific instruments (investment_researcher's lane).
#
# Posture: GREEN per ADR-0092 Decision 2 — anti-recommendation
# arbitration is non-acting; forbid_operator_blocking +
# forbid_transaction_execution + forbid_investment_advice
# policies enforce role separation at governance layer regardless
# of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0092 Phase A — Birth RiskAdvisor-D6"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load risk_advisor role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing RiskAdvisor-D6"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='RiskAdvisor-D6']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      RiskAdvisor-D6 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing RiskAdvisor-D6 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "risk_advisor",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "RiskAdvisor-D6",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-risk-advisor-d6" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      RiskAdvisor-D6 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting RiskAdvisor-D6's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-risk-advisor-d6-init" \
  -d '{"posture": "green", "reason": "ADR-0092 Decision 2 — risk_advisor anti-recommendation arbitration is non-acting; forbid_operator_blocking + forbid_transaction_execution + forbid_investment_advice policies enforce role separation regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "RiskAdvisor-D6 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          risk_advisor"
echo "  genre:         guardian"
echo "  posture:       green"
echo "  skill:         risk_analysis.v1"
echo "  next steps:    seed a candidate_action memory_write + dispatch"
echo "                 risk_analysis with a candidate_id to"
echo "                 compose an anti-recommendation alert."
echo "                 Operator picks up the alert; operator decides."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
