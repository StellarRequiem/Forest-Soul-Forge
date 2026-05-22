#!/bin/bash
# ADR-0066 Phase D (B457) — birth PurplePete-D3 (purple_pete role).
#
# Mirrors birth-detection-engineer.command's 4-phase shape.
# Idempotent. Posture: YELLOW per ADR-0078 Decision 5 — purple_pete
# starts YELLOW, the operator promotes to GREEN after reviewing the
# first few exercise reports. RED is automatic if a scenario ever
# targets a real system outside the sandbox boundary.
#
# purple_pete is the researcher-genre agent that runs synthetic
# attack scenarios (config/purple_pete_scenarios/) against the SOC's
# detection coverage. It writes ONLY the simulation telemetry store
# and dispatches NO real response — its constitution's three
# policies (forbid_production_telemetry_emit,
# forbid_real_response_dispatch, require_scenario_provenance) bound
# the simulation boundary.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0066 Phase D — Birth PurplePete-D3"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load purple_pete role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing PurplePete-D3"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='PurplePete-D3']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      PurplePete-D3 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing PurplePete-D3 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "purple_pete",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "PurplePete-D3",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 30 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-purple-pete-d3" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      PurplePete-D3 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/4] Verifying PurplePete-D3 constitution parses cleanly"
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
echo "[4/4] Setting posture: YELLOW (operator promotes after first reports)"
POSTURE_RESP=$(curl -s --max-time 10 "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-purple-pete-d3-init" \
  -d '{"posture":"yellow","reason":"ADR-0078 Decision 5: purple_pete starts YELLOW. Operator promotes to GREEN after reviewing the first few exercise reports; RED is automatic if a scenario targets a real system outside the sandbox. External reach (web_fetch for ATT&CK refs) stays gated until the operator allowlists the technique catalog URL."}' \
  2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "PurplePete-D3 birth complete"
echo "=========================================================="
echo
echo "  instance_id:   $INSTANCE_ID"
echo "  role:          purple_pete"
echo "  genre:         researcher"
echo "  posture:       yellow (operator promotes after first reports)"
echo "  skill:         purple_team_brief.v1 (operator-driven review)"
echo
echo "Operator next step:"
echo "  Author synthetic scenarios in config/purple_pete_scenarios/."
echo "  purple_pete replays them through the production DetectionEngine"
echo "  in simulation mode (writes only data/telemetry_simulation.sqlite)"
echo "  and emits purple_team_run_completed coverage measurements."
echo "  See docs/runbooks/soar-playbooks.md."
echo
echo "Press any key to close."
read -n 1 || true
