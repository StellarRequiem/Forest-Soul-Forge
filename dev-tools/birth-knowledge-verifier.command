#!/bin/bash
# ADR-0086 Phase C — birth KnowledgeVerifier-D1 (knowledge_verifier
# role).
#
# Guardian-genre single-agent contradiction auditor for D1
# Knowledge Forge. Walks the calling agent's own private + lineage
# memory for contradictions; flags via memory_flag_contradiction.v1
# (ADR-0036 T2). Single-agent scope per ADR-0086 Decision 3.
#
# YELLOW posture per ADR-0086 Decision 1 — every flagged
# contradiction surfaces to the operator for review before
# propagating to downstream domains (D9/D10/D7).
#
# Per-tool constraint patches:
#   None — verifier's kit is memory-only + chain-only;
#   no per-tool path/host constraints needed.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0086 Phase C — Birth KnowledgeVerifier-D1"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load knowledge_verifier role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing KnowledgeVerifier-D1"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='KnowledgeVerifier-D1']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      KnowledgeVerifier-D1 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing KnowledgeVerifier-D1 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "knowledge_verifier",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "KnowledgeVerifier-D1",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-knowledge-verifier-d1" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      KnowledgeVerifier-D1 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting KnowledgeVerifier-D1's posture to YELLOW"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-knowledge-verifier-d1-init" \
  -d '{"posture": "yellow", "reason": "ADR-0086 Decision 1 — knowledge_verifier defaults to YELLOW so flagged contradictions surface to the operator for review before propagating to downstream domains. Move to GREEN only after flag-quality bed-in (operator_duties in constitution)."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "KnowledgeVerifier-D1 ready. Summary:"
echo "  instance_id:    ${INSTANCE_ID}"
echo "  role:           knowledge_verifier"
echo "  genre:          guardian"
echo "  posture:        yellow  (operator-gated)"
echo "  scope:          single_agent (ADR-0086 Decision 3)"
echo "  next steps:     run knowledge_contradiction_flag skill"
echo "                  dispatch against an existing topic that"
echo "                  Librarian-D1 has cataloged."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
