#!/bin/bash
# ADR-0088 Phase C — birth Editor-D7 (editor role).
#
# Guardian-genre editing + format-adaptation role for D7 Content
# Studio. Composes verify_claim (fact-check; ADR-0063) +
# voice_match_check (voice gate; ADR-0088 Phase B) + format_adapt
# (target-format rewrite; Phase C) over the writer's primary
# draft. Produces editorial verdict + format-adapted artifact
# alongside; NEVER mutates the source draft.
#
# Posture: GREEN per ADR-0088 Decision 1 — editorial composition
# is read-only end-to-end; forbid_source_draft_mutation +
# forbid_publish policies enforce the source-protection + no-
# publish invariants at governance layer regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0088 Phase C — Birth Editor-D7"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load editor role + format_adapt tool"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing Editor-D7"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Editor-D7']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Editor-D7 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Editor-D7 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "editor",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Editor-D7",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-editor-d7" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Editor-D7 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting Editor-D7's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-editor-d7-init" \
  -d '{"posture": "green", "reason": "ADR-0088 Decision 1 — editor read-only end-to-end (composes verify_claim + voice_match_check + format_adapt over writer drafts; produces verdict + new artifact, NEVER mutates source); forbid_source_draft_mutation + forbid_publish policies enforce invariants regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Editor-D7 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          editor"
echo "  genre:         guardian"
echo "  posture:       green"
echo "  skills:        editing.v1, format_adaptation.v1"
echo "  next steps:    1. Dispatch editing on a writer draft to"
echo "                    get a verdict (approve/revise/reject)."
echo "                 2. Dispatch format_adaptation to produce"
echo "                    twitter_thread / linkedin_post /"
echo "                    newsletter / blog variants."
echo "                 3. Approved drafts hand off to Phase D's"
echo "                    distribution_pilot (YELLOW)."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
