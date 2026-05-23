#!/bin/bash
# ADR-0089 Phase B — birth Assessor-D9 (assessor role).
#
# Guardian-genre measurement role for D9 Learning Coach. Composes
# knowledge_assessment.v1 (item generation) + assessment_score.v1
# (deterministic response scoring) + verify_claim.v1 (Reality Anchor)
# into an assessment pass over operator responses. Persists
# misconception PROPOSALS via memory_write (private scope); operator
# dispatches misconception_log.v1 directly post-review to commit.
# Same separation-of-duties pattern as D1's knowledge_verifier.
#
# Posture: YELLOW per ADR-0089 Decision 3 — every non-read-only
# dispatch queues for operator approval. Even at GREEN, the per-
# call human approval gate on misconception_log.v1 (filesystem)
# remains load-bearing.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0089 Phase B — Birth Assessor-D9"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load assessor role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing Assessor-D9"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Assessor-D9']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Assessor-D9 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Assessor-D9 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "assessor",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Assessor-D9",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-assessor-d9" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Assessor-D9 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting Assessor-D9's posture to YELLOW"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-assessor-d9-init" \
  -d '{"posture": "yellow", "reason": "ADR-0089 Decision 3 — assessor defaults YELLOW; every non-read-only dispatch queues for operator approval. Misconception ledger writes operator-dispatched via misconception_log.v1 directly (kit omits it; same knowledge_verifier separation pattern)."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Assessor-D9 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          assessor"
echo "  genre:         guardian"
echo "  posture:       yellow"
echo "  skills:        knowledge_assessment.v1 + misconception_tracking.v1"
echo "  next steps:    dispatch knowledge_assessment with topic_slug +"
echo "                 operator response + ground_truth_answers to score."
echo "                 On incorrect/partial verdict, dispatch"
echo "                 misconception_tracking.v1 to compose a proposal,"
echo "                 then dispatch misconception_log.v1 directly to"
echo "                 commit the proposal to the persistent ledger."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
