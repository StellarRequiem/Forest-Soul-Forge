#!/bin/bash
# ADR-0066 Phase D (B456) — birth PlaybookPilot-D3 (playbook_pilot role).
#
# Mirrors birth-detection-engineer.command's 4-phase shape.
# Idempotent. Posture: YELLOW per ADR-0078 Decision 5 — every SOAR
# action is operator-gated regardless of posture; YELLOW adds
# bedding-in friction while the operator reviews the first runs.
#
# playbook_pilot is the actuator-genre agent that consumes
# detection_fired events and runs operator-authored playbooks
# (config/playbooks/) under approval governance. Its Phase D kit
# is the review-and-oversight surface (playbook_run_review.v1);
# the PlaybookEngine substrate is the resolve-and-record action
# layer.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0066 Phase D — Birth PlaybookPilot-D3"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load playbook_pilot role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing PlaybookPilot-D3"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='PlaybookPilot-D3']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      PlaybookPilot-D3 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing PlaybookPilot-D3 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "playbook_pilot",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "PlaybookPilot-D3",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 30 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-playbook-pilot-d3" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      PlaybookPilot-D3 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/4] Verifying PlaybookPilot-D3 constitution parses cleanly"
DETAIL=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null)
CONST_PATH=$(echo "$DETAIL" | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)
if [ -n "$CONST_PATH" ] && [ -f "$CONST_PATH" ]; then
  VENV_PY="$(pwd)/.venv/bin/python3"
  if "$VENV_PY" -c "import yaml; yaml.safe_load(open('$CONST_PATH'))" >/dev/null 2>&1; then
    echo "      Constitution parses cleanly"
  else
    echo "      ERROR: constitution YAML parse failed."
    exit 3
  fi
else
  echo "      WARN: could not resolve constitution_path"
fi

echo
echo "[4/4] Setting posture: YELLOW (operator-gated bedding-in)"
POSTURE_RESP=$(curl -s --max-time 10 "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-playbook-pilot-d3-init" \
  -d '{"posture":"yellow","reason":"ADR-0078 Decision 5: playbook_pilot stays YELLOW. Every SOAR action is operator-gated regardless of posture (ADR-0066 D2 default-deny); YELLOW adds bedding-in friction while the operator reviews the first playbook runs and confirms the steps_auto_approved allowlists."}' \
  2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "PlaybookPilot-D3 birth complete"
echo "=========================================================="
echo
echo "  instance_id:   $INSTANCE_ID"
echo "  role:          playbook_pilot"
echo "  genre:         actuator"
echo "  posture:       yellow (operator-gated; SOAR actions always gated)"
echo "  skill:         playbook_run_review.v1 (operator-driven review)"
echo
echo "Operator next step:"
echo "  Author response playbooks in config/playbooks/. The pilot"
echo "  consumes detection_fired events + runs matching playbooks"
echo "  under approval governance — every state-changing step is"
echo "  default-deny unless listed in the playbook's"
echo "  approval.steps_auto_approved. See"
echo "  docs/runbooks/soar-playbooks.md."
echo
echo "Press any key to close."
read -n 1 || true
