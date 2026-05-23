#!/bin/bash
# ADR-0089 Phase A — birth Mentor-D9 (mentor role).
#
# Researcher-genre coaching role for D9 Learning Coach. Composes
# operator-facing coaching narratives from operator profile +
# D1 catalog + lineage memory (curriculum drafts, assessment
# scores). Read-only — NEVER gates progression (assessor's lane,
# D9 Phase B); NEVER mutates the curriculum DAG (curriculum_designer's
# lane).
#
# Posture: GREEN per ADR-0089 Decision 1 — coaching-narrative
# composition is non-acting; forbid_progression_gating +
# forbid_curriculum_mutation policies enforce role separation at
# governance layer regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0089 Phase A — Birth Mentor-D9"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load mentor role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing Mentor-D9"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Mentor-D9']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Mentor-D9 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Mentor-D9 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "mentor",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Mentor-D9",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-mentor-d9" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Mentor-D9 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting Mentor-D9's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-mentor-d9-init" \
  -d '{"posture": "green", "reason": "ADR-0089 Decision 1 — mentor coaching-narrative composition is non-acting; forbid_progression_gating + forbid_curriculum_mutation policies enforce role separation regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Mentor-D9 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          mentor"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         coaching.v1"
echo "  next steps:    dispatch coaching with a topic_slug to compose"
echo "                 a coaching brief. The CurriculumDesigner-D9's"
echo "                 attestation on the same topic_slug feeds in"
echo "                 via memory_recall."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
