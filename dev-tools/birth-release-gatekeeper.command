#!/bin/bash
# ADR-0077 T2b — birth ReleaseGatekeeper-D4 (release_gatekeeper).
#
# Per-tool constraint patches (ADR-0077 §release_gatekeeper kit):
#   shell_exec: allowed_commands=["pytest","fsf"], no git/pip/curl
#
# Posture: green. The gate emits pass/fail decisions freely;
# the actual gate is the operator's tag-the-release step, which
# is outside this agent's surface. Constitutional policy
# forbid_release_action blocks the act-of-release tools at the
# kit layer.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0077 T2b — Birth ReleaseGatekeeper-D4 (release_gatekeeper)"
echo "=========================================================="

# ---------------------------------------------------------------------------
# 1. Restart daemon.
# ---------------------------------------------------------------------------
echo
echo "[1/4] Restarting daemon to load release_gatekeeper role"
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
echo "[2/4] Checking for existing ReleaseGatekeeper-D4"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='ReleaseGatekeeper-D4']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      ReleaseGatekeeper-D4 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing ReleaseGatekeeper-D4 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "release_gatekeeper",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "ReleaseGatekeeper-D4",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 30 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      ReleaseGatekeeper-D4 born: instance_id=${INSTANCE_ID}"
fi

# ---------------------------------------------------------------------------
# 3. Patch constitution with per-tool constraints.
# ---------------------------------------------------------------------------
echo
echo "[3/4] Patching ReleaseGatekeeper-D4's constitution with per-tool constraints"
CONST_PATH=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)

if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
  echo "      Skipping constraint patch — manual follow-up required."
else
  echo "      Constitution at: $CONST_PATH"
  VENV_PY="$(pwd)/.venv/bin/python3"
  "$VENV_PY" - "$CONST_PATH" <<'PY'
import sys, yaml
path = sys.argv[1]
with open(path) as f:
    doc = yaml.safe_load(f)
patched = False
for entry in doc.get("tools") or []:
    if not isinstance(entry, dict): continue
    name = entry.get("name", "")
    constraints = entry.setdefault("constraints", {}) or {}
    if name == "shell_exec":
        # Read-only gate. pytest runs the conformance suite; fsf
        # runs the chain verify / drift sentinel / changelog
        # checker. NO git (no tagging), NO pip (no dep mutation),
        # NO curl (no publishing).
        constraints["allowed_commands"] = ["pytest", "fsf"]
        constraints["forbidden_commands"] = [
            "git", "pip", "rm", "dd", "mkfs", "sudo",
            "curl", "wget", "npm", "twine",
        ]
        patched = True
if patched:
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print("      Constraints patched: shell_exec")
else:
    print("      (no matching tools found for constraint patch)")
PY
fi

# ---------------------------------------------------------------------------
# 4. Set posture GREEN.
# ---------------------------------------------------------------------------
# Rationale: the gate emits pass/fail freely; the actual gate is
# the operator's tag-the-release step, outside this agent's
# surface. forbid_release_action constitutional policy blocks
# the dangerous tools at the kit layer regardless of posture.
echo
echo "[4/4] Setting ReleaseGatekeeper-D4's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 5 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"posture": "green", "reason": "ADR-0077 — gate emits decisions; operator tag-time is the actual gate. forbid_release_action constitutional policy blocks dangerous tools at kit layer"}' 2>&1)
echo "      Posture response:"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "ReleaseGatekeeper-D4 ready. Summary:"
echo "  instance_id:    ${INSTANCE_ID}"
echo "  role:           release_gatekeeper"
echo "  genre:          guardian"
echo "  posture:        green"
echo
echo "D4 advanced rollout T2b complete — all three agents birthed."
echo "Next: T3 handoffs.yaml wiring + cascade rule + integration"
echo "test (commit-burst334)."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true  # EOF-tolerant for non-interactive callers
