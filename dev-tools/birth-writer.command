#!/bin/bash
# ADR-0088 Phase A — birth Writer-D7 (writer role).
#
# Researcher-genre drafting role for D7 Content Studio. Composes
# long-form drafts from research briefs + outlines; writes drafts
# to private memory tagged for downstream editor + style_steward
# pickup. Read-only — never publishes, never adapts formats.
#
# Posture: GREEN per ADR-0088 Decision 1 — drafts-to-private-memory
# is non-acting; forbid_publish + forbid_format_adaptation policies
# enforce the drafts-only stance at governance layer regardless
# of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0088 Phase A — Birth Writer-D7"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load writer role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing Writer-D7"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Writer-D7']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Writer-D7 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Writer-D7 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "writer",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Writer-D7",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-writer-d7" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Writer-D7 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting Writer-D7's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-writer-d7-init" \
  -d '{"posture": "green", "reason": "ADR-0088 Decision 1 — writer drafts-to-private-memory is non-acting; forbid_publish + forbid_format_adaptation policies enforce drafts-only stance regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Writer-D7 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          writer"
echo "  genre:         researcher"
echo "  posture:       green"
echo "  skill:         draft_writing.v1"
echo "  next steps:    dispatch draft_writing with an outline +"
echo "                 topic_slug to compose a long-form draft."
echo "                 The ContentResearcher-D7's brief on the"
echo "                 same topic_slug feeds in via memory_recall."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
