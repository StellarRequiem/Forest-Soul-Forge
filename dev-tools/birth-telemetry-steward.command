#!/bin/bash
# ADR-0064 T4 (B379) — birth TelemetryStreward-D3 (telemetry_steward role).
#
# Mirrors birth-forensic-archivist.command's 4-phase shape: daemon
# kickstart -> birth POST -> constitution patch (no per-tool
# constraints needed; the kit is fully self-contained) -> posture
# set -> summary. Idempotent: re-runs skip the birth if
# TelemetryStreward-D3 already exists.
#
# Posture: GREEN per ADR-0064 D7 — steward observes, never acts.
# Same posture rationale as forensic_archivist (chain-of-custody
# verifies-not-mutates is the parallel discipline).
#
# Per-tool constraint patches:
#   memory_write: no path constraint (writes to registry SQLite).
#   memory_recall: no path constraint.
#   audit_chain_verify: no constraints.
#   delegate / llm_think / text_summarize: no constraints.
# The steward's kit is genuinely allowlist-less because it has
# no filesystem reach beyond its own memory + the audit chain
# (which audit_chain_verify navigates via the daemon-bound chain
# instance, not via raw file I/O the steward could redirect).

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0064 T4 — Birth TelemetryStreward-D3"
echo "=========================================================="

# ---------------------------------------------------------------------------
# 1. Restart daemon so it picks up the new telemetry_steward role.
# ---------------------------------------------------------------------------
echo
echo "[1/4] Restarting daemon to load telemetry_steward role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

# ---------------------------------------------------------------------------
# 2. Check existence; birth if absent.
# ---------------------------------------------------------------------------
echo
echo "[2/4] Checking for existing TelemetryStreward-D3"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='TelemetryStreward-D3']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      TelemetryStreward-D3 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing TelemetryStreward-D3 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "telemetry_steward",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "TelemetryStreward-D3",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 30 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-telemetry-steward-d3" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    echo "      Likely causes:"
    echo "        - trait_engine hasn't picked up telemetry_steward"
    echo "          (kickstart timing — wait 10s and retry)"
    echo "        - constitution_templates.yaml typo blocking role_base"
    echo "          resolution"
    echo "        - tool_catalog telemetry_steward archetype missing"
    echo "      Check daemon logs."
    exit 2
  fi
  echo "      TelemetryStreward-D3 born: instance_id=${INSTANCE_ID}"
fi

# ---------------------------------------------------------------------------
# 3. Confirm constitution loaded cleanly (steward has no per-tool
#    path constraints, so no patch needed — just verify parse).
# ---------------------------------------------------------------------------
echo
echo "[3/4] Verifying TelemetryStreward-D3 constitution loads cleanly"
DETAIL=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null)
CONST_PATH=$(echo "$DETAIL" | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)
if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
  echo "      The agent is born but the registry didn't return a path."
else
  echo "      Constitution at: $CONST_PATH"
  VENV_PY="$(pwd)/.venv/bin/python3"
  if "$VENV_PY" -c "import yaml; yaml.safe_load(open('$CONST_PATH'))" >/dev/null 2>&1; then
    echo "      Constitution parses cleanly"
  else
    echo "      ERROR: constitution YAML parse failed. Investigation needed."
    "$VENV_PY" -c "import yaml; yaml.safe_load(open('$CONST_PATH'))" 2>&1 | tail -5
    exit 3
  fi
fi

# ---------------------------------------------------------------------------
# 4. Set posture to GREEN.
# ---------------------------------------------------------------------------
echo
echo "[4/4] Setting posture: GREEN"
POSTURE_RESP=$(curl -s --max-time 10 "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-telemetry-steward-d3-init" \
  -d '{"posture":"green","reason":"ADR-0064 T4: telemetry_steward observes batch metadata only; never acts on findings. Read-only kit; GREEN per D7."}' \
  2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "TelemetryStreward-D3 birth complete"
echo "=========================================================="
echo
echo "  instance_id:   $INSTANCE_ID"
echo "  role:          telemetry_steward"
echo "  genre:         guardian"
echo "  posture:       green"
echo "  skill:         telemetry_steward_brief.v1 (operator-driven)"
echo
echo "  next steps:"
echo "    operator dispatches telemetry_steward_brief.v1 against"
echo "    recent_batches list pulled from the audit chain."
echo
echo "Press any key to close."
read -n 1 || true
