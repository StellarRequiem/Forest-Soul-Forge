#!/bin/bash
# ADR-0088 Phase B — birth StyleSteward-D7 (style_steward role).
#
# Guardian-genre voice arbiter for D7 Content Studio. Builds +
# maintains the operator voice profile (voice_profile_build.v1)
# and scores drafts against it (voice_match_check.v1). Read-only —
# NEVER rewrites drafts; flags + spans only.
#
# Posture: GREEN per ADR-0088 Decision 1 — voice arbitration is
# non-acting; forbid_draft_rewrite policy enforces the
# flag-not-rewrite invariant at governance layer regardless of
# posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0088 Phase B — Birth StyleSteward-D7"
echo "=========================================================="

echo
echo "[1/3] Restarting daemon to load style_steward role + voice tools"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 7
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/3] Checking for existing StyleSteward-D7"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='StyleSteward-D7']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      StyleSteward-D7 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing StyleSteward-D7 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "style_steward",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "StyleSteward-D7",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-style-steward-d7" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      StyleSteward-D7 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/3] Setting StyleSteward-D7's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-style-steward-d7-init" \
  -d '{"posture": "green", "reason": "ADR-0088 Decision 1 — style_steward voice arbitration is non-acting; forbid_draft_rewrite + forbid_silent_profile_override policies enforce flag-not-rewrite invariant regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "StyleSteward-D7 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          style_steward"
echo "  genre:         guardian"
echo "  posture:       green"
echo "  skills:        voice_profile_build.v1, voice_matching.v1"
echo "  next steps:    1. Operator pastes 3-10 prior writing"
echo "                    samples into private memory tagged"
echo "                    'voice_sample' (via memory_write or"
echo "                    a future curator UI)."
echo "                 2. Dispatch voice_profile_build to derive"
echo "                    the stylometric profile."
echo "                 3. Dispatch voice_matching after a draft"
echo "                    lands to score voice fidelity."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
