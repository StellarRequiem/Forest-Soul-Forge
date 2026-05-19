#!/bin/bash
# ADR-0081 T3 (B396) — birth WiringSentinel (wiring_sentinel role).
#
# Singleton-per-forest guardian. Mirrors birth-detection-engineer
# 4-phase shape. Idempotent. Posture: GREEN — the sentinel runs
# entirely on local disk + audit chain + section-15 output. No
# network reach. No external dependencies.
#
# Singleton-enforcement: /birth refuses a second wiring_sentinel
# spawn with 409 once one exists. Operator archives via
# POST /agents/archive before spawning a replacement.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0081 T3 — Birth WiringSentinel"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load wiring_sentinel role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing WiringSentinel"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='WiringSentinel']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      WiringSentinel already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing WiringSentinel — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "wiring_sentinel",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "WiringSentinel",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 30 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-wiring-sentinel" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      WiringSentinel born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/4] Verifying WiringSentinel constitution parses cleanly"
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
echo "[4/4] Setting posture: GREEN (read-only sentinel, no external reach)"
POSTURE_RESP=$(curl -s --max-time 10 "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-wiring-sentinel-init" \
  -d '{"posture":"green","reason":"ADR-0081 T3: wiring_sentinel runs entirely on local disk + audit chain + section-15 coverage.json output. No network reach, no external dependencies. Guardian-genre read_only ceiling. Singleton enforced at /birth time."}' \
  2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "WiringSentinel birth complete"
echo "=========================================================="
echo
echo "  instance_id:   $INSTANCE_ID"
echo "  role:          wiring_sentinel"
echo "  genre:         guardian"
echo "  posture:       green (read-only, no external reach)"
echo "  skill:         wiring_audit.v1 (scheduled 4-hour cadence per ADR-0081 D7)"
echo
echo "Operator next step:"
echo "  After T5 lands (scheduled task forest-soul-forge-wiring-audit),"
echo "  the sentinel runs wiring_audit.v1 every 4 hours against"
echo "  data/test-runs/diagnostic-15-wiring-cross-check/coverage.json."
echo "  Medium+ gaps escalate to the operator queue via delegate.v1;"
echo "  info/low surface only in the next diagnostic-all run."
echo
echo "Press any key to close."
read -n 1 || true
