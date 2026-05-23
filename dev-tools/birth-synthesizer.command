#!/bin/bash
# ADR-0086 Phase B — birth Synthesizer-D1 (synthesizer role).
#
# Researcher-genre synthesis role for D1 Knowledge Forge. Reads
# the Librarian-D1 catalog, builds topic graphs via
# topic_genealogy_build.v1, and produces daily-delta synthesis
# (daily_knowledge_delta.v1 lands in Phase D). Read-only
# synthesis end-to-end.
#
# Per-tool constraint patches (ADR-0086 §synthesizer kit):
#   None — synthesizer's kit is memory-only + tool-only;
#   no per-tool path/host constraints needed.
#
# Posture: GREEN per ADR-0086 Decision 1 — read-only synthesis
# is non-acting; provenance discipline enforced by constitution
# policy regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0086 Phase B — Birth Synthesizer-D1"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load synthesizer role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing Synthesizer-D1"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Synthesizer-D1']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Synthesizer-D1 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Synthesizer-D1 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "synthesizer",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Synthesizer-D1",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-synthesizer-d1" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Synthesizer-D1 born: instance_id=${INSTANCE_ID}"
fi

mkdir -p "$(pwd)/data/knowledge/synthesis"

echo
echo "[3/3] Setting Synthesizer-D1's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-synthesizer-d1-init" \
  -d '{"posture": "green", "reason": "ADR-0086 Decision 1 — synthesizer read-only synthesis is non-acting; provenance discipline enforced by constitution policy regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Synthesizer-D1 ready. Summary:"
echo "  instance_id:    ${INSTANCE_ID}"
echo "  role:           synthesizer"
echo "  genre:          researcher"
echo "  posture:        green"
echo "  synthesis root: data/knowledge/synthesis/ (created if absent)"
echo "  next steps:     run topic_genealogy or knowledge_summarize"
echo "                  skill dispatch against the Librarian-D1 catalog."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
