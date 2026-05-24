#!/bin/bash
# ADR-0092 Phase C — birth InvestmentResearcher-D6 (investment_researcher role).
#
# Researcher-genre investment research role for D6 Personal Finance
# Guardian. Dispatches investment_compare.v1 over operator-supplied
# option records + composes side-by-side comparison attestations
# for operator pickup. Read-only — NEVER advises which option to
# pick (manifest's "info-only, never advice" floor); NEVER executes
# trades (manifest hard rule); NEVER composes burn-rate narratives
# (budget_analyst's lane); NEVER categorizes transactions
# (transaction_tracker's lane).
#
# Posture: GREEN per ADR-0092 Decision 1 — comparison composition
# is non-acting and info-only; forbid_investment_advice +
# forbid_transaction_execution policies enforce the manifest's
# info-only floor at governance layer regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0092 Phase C — Birth InvestmentResearcher-D6"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load investment_researcher role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing InvestmentResearcher-D6"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='InvestmentResearcher-D6']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      InvestmentResearcher-D6 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing InvestmentResearcher-D6 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "investment_researcher",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "InvestmentResearcher-D6",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-investment-researcher-d6" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      InvestmentResearcher-D6 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting InvestmentResearcher-D6's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-investment-researcher-d6-init" \
  -d '{"posture": "green", "reason": "ADR-0092 Decision 1 — investment_researcher comparison composition is non-acting + info-only; forbid_investment_advice + forbid_transaction_execution policies enforce the manifest info-only floor regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "InvestmentResearcher-D6 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          investment_researcher"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         investment_research.v1"
echo "  next steps:    seed an option_corpus memory + dispatch"
echo "                 investment_research with a comparison_slug +"
echo "                 options + dimensions to compose a side-by-"
echo "                 side comparison brief. Operator picks up"
echo "                 the brief; operator decides — the brief"
echo "                 surfaces winners + deltas, NEVER advises."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
