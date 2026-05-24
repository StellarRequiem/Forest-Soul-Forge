#!/bin/bash
# ADR-0092 Phase B — birth TransactionTracker-D6 (transaction_tracker role).
#
# Researcher-genre transaction categorization role for D6 Personal
# Finance Guardian. Dispatches transaction_categorize.v1 over
# operator-supplied transaction batches + operator-supplied
# category rules + composes categorization attestations tagged
# for budget_analyst + risk_advisor pickup. Read-only — NEVER
# charges anything (manifest hard rule); NEVER composes burn-rate
# narratives (budget_analyst's lane); NEVER advises on
# investments (investment_researcher's lane).
#
# Posture: GREEN per ADR-0092 Decision 1 — categorization is
# non-acting; forbid_transaction_execution +
# forbid_burn_rate_composition + forbid_investment_advice
# policies enforce role separation at governance layer regardless
# of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0092 Phase B — Birth TransactionTracker-D6"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load transaction_tracker role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing TransactionTracker-D6"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='TransactionTracker-D6']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      TransactionTracker-D6 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing TransactionTracker-D6 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "transaction_tracker",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "TransactionTracker-D6",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-transaction-tracker-d6" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      TransactionTracker-D6 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting TransactionTracker-D6's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-transaction-tracker-d6-init" \
  -d '{"posture": "green", "reason": "ADR-0092 Decision 1 — transaction_tracker categorization is non-acting; forbid_transaction_execution + forbid_burn_rate_composition + forbid_investment_advice policies enforce role separation regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "TransactionTracker-D6 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          transaction_tracker"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         transaction_monitoring.v1"
echo "  next steps:    seed a rule_corpus memory + dispatch"
echo "                 transaction_monitoring with a transaction"
echo "                 batch + rules to compose a categorization"
echo "                 attestation. BudgetAnalyst-D6 picks up via"
echo "                 the transaction_categorized:<batch_slug> tag."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
