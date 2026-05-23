#!/bin/bash
# ADR-0085 Phase D — birth ReportGenerator-D8 (report_generator role).
#
# Researcher genre, GREEN posture default per ADR-0085 Decision 5
# — synthesis is non-acting; the operator decides what to do with
# the packet. Idempotent: re-runs skip the birth if
# ReportGenerator-D8 exists.
#
# Per-tool constraint patches:
#   code_read: allowed_paths to config/compliance_frameworks/ +
#              data/compliance/ + the audit chain (the corpora
#              the packet draws from).
#   audit_packet_generate: no path constraint (the tool's own
#                          framework_id regex is the gate).

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0085 Phase D — Birth ReportGenerator-D8"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load report_generator role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing ReportGenerator-D8"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='ReportGenerator-D8']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      ReportGenerator-D8 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing ReportGenerator-D8 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "report_generator",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "ReportGenerator-D8",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-report-generator-d8" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      ReportGenerator-D8 born: instance_id=${INSTANCE_ID}"
fi

echo
echo "[3/4] Patching ReportGenerator-D8's constitution with per-tool constraints"
CONST_PATH=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)

if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
else
  echo "      Constitution at: $CONST_PATH"
  VENV_PY="$(pwd)/.venv/bin/python3"
  "$VENV_PY" - "$CONST_PATH" <<'PY'
import sys, yaml
path = sys.argv[1]
with open(path) as f:
    doc = yaml.safe_load(f)
patched = []
for entry in doc.get("tools") or []:
    if not isinstance(entry, dict):
        continue
    name = entry.get("name", "")
    constraints = entry.setdefault("constraints", {}) or {}
    if name == "code_read":
        constraints["allowed_paths"] = [
            "config/compliance_frameworks/",
            "data/compliance/",
            "examples/audit_chain.jsonl",
            "examples/segments/",
        ]
        constraints["forbidden_paths"] = [
            ".env",
            "~/.fsf/secrets",
            "data/registry.sqlite",
            "src/",
        ]
        patched.append("code_read")
if patched:
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print(f"      Constraints patched: {', '.join(patched)}")
else:
    print("      (no matching tools found for constraint patch)")
PY
fi

echo
echo "[4/4] Setting ReportGenerator-D8's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-report-generator-d8-init" \
  -d '{"posture": "green", "reason": "ADR-0085 Decision 5 — packet synthesis is non-acting; the operator decides what to do with the packet (file / archive / share)."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "ReportGenerator-D8 ready. Summary:"
echo "  instance_id:    ${INSTANCE_ID}"
echo "  role:           report_generator"
echo "  genre:          researcher"
echo "  posture:        green"
echo "  packet output:  via tool result (operator persists)"
echo "  cascade:        d4_code_review→d8 + d3_local_soc→d8 already wired"
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
