#!/bin/bash
# ADR-0089 Phase A — birth CurriculumDesigner-D9 (curriculum_designer role).
#
# Researcher-genre planning role for D9 Learning Coach. Dispatches
# curriculum_design.v1 over operator-goal + operator-curated catalog
# (typically D1 catalog reads via memory_recall) to compose a topic-
# prereq DAG + deterministic ordered learning path. Writes attestations
# tagged for mentor + assessor + spaced_repetition_pilot pickup.
# Read-only — NEVER assesses understanding (assessor's lane, D9 Phase B).
#
# Posture: GREEN per ADR-0089 Decision 1 — deterministic-DAG composition
# is non-acting; forbid_assessment + forbid_silent_catalog_substitution
# policies enforce the operator-curated-source discipline regardless of
# posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0089 Phase A — Birth CurriculumDesigner-D9"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load curriculum_designer role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing CurriculumDesigner-D9"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='CurriculumDesigner-D9']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      CurriculumDesigner-D9 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing CurriculumDesigner-D9 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "curriculum_designer",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "CurriculumDesigner-D9",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-curriculum-designer-d9" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      CurriculumDesigner-D9 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting CurriculumDesigner-D9's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-curriculum-designer-d9-init" \
  -d '{"posture": "green", "reason": "ADR-0089 Decision 1 — curriculum_designer deterministic-DAG composition is non-acting; forbid_assessment + forbid_silent_catalog_substitution policies enforce operator-curated-source discipline regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "CurriculumDesigner-D9 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          curriculum_designer"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         curriculum_design.v1"
echo "  next steps:    dispatch curriculum_design with a goal_topic"
echo "                 + goal_topic_slug + an operator-curated catalog"
echo "                 (typically D1 catalog entries via memory_recall)"
echo "                 to compose a learning path. Mentor-D9's coaching"
echo "                 skill picks the curriculum up via memory_recall."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
