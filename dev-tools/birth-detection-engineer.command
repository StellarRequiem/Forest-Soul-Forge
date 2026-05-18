#!/bin/bash
# ADR-0065 T3 (B391) — birth DetectionEngineer-D3 (detection_engineer role).
#
# Mirrors birth-threat-intel-curator.command's 4-phase shape.
# Idempotent. Posture: YELLOW per ADR-0065 T3 — same posture
# rationale as threat_intel_curator (external reach via web_fetch
# gated until operator configures allowlisted ATT&CK URLs).
#
# Per-tool constraints set at birth time:
#   web_fetch: allowed_domains empty -> operator MUST add the
#     ATT&CK technique catalog URL via posture / constitution
#     patch before any propose_detection skill call. This is the
#     forbid_silent_feed_substitution discipline mirrored from
#     threat_intel_curator.
#   Other tools: standard read-only constraints from the kit.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0065 T3 — Birth DetectionEngineer-D3"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load detection_engineer role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing DetectionEngineer-D3"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='DetectionEngineer-D3']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      DetectionEngineer-D3 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing DetectionEngineer-D3 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "detection_engineer",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "DetectionEngineer-D3",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 30 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-detection-engineer-d3" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      DetectionEngineer-D3 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/4] Verifying DetectionEngineer-D3 constitution parses cleanly"
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
echo "[4/4] Setting posture: YELLOW (until operator configures ATT&CK allowlist)"
POSTURE_RESP=$(curl -s --max-time 10 "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-detection-engineer-d3-init" \
  -d '{"posture":"yellow","reason":"ADR-0065 T3: external reach (web_fetch for ATT&CK refs) gated YELLOW until operator allowlists the technique catalog URL. Operator promotes to GREEN after the allowlist is in place."}' \
  2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "DetectionEngineer-D3 birth complete"
echo "=========================================================="
echo
echo "  instance_id:   $INSTANCE_ID"
echo "  role:          detection_engineer"
echo "  genre:         researcher"
echo "  posture:       yellow (operator promotes after ATT&CK allowlist)"
echo "  skill:         propose_detection.v1 (operator-driven)"
echo
echo "Operator next step:"
echo "  Allowlist https://attack.mitre.org/techniques/ via web_fetch"
echo "  constraints (constitution patch or posture override) before"
echo "  the first propose_detection call. propose_detection passes"
echo "  the URL through inputs; ATT&CK reference fetch fails cleanly"
echo "  if the domain isn't allowlisted."
echo
echo "Press any key to close."
read -n 1 || true
