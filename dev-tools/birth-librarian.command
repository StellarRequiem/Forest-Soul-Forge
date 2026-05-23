#!/bin/bash
# ADR-0086 Phase A — birth Librarian-D1 (librarian role).
#
# Guardian-genre catalog discipline role for D1 Knowledge Forge.
# Owns the operator's knowledge catalog + per-fact provenance
# ledger. NEVER mutates source data — sibling pattern to
# knowledge_consolidator but for sustained catalog discipline.
#
# Per-tool constraint patches (ADR-0086 §librarian kit):
#   code_read:       allowed_paths to operator-stored notes
#                    directories (data/knowledge/ + operator-configured
#                    notes roots when forest-files / forest-notes
#                    connectors are absent — graceful degradation
#                    per ADR-0086 Decision 4).
#   memory_write / memory_recall: no path constraint (private memory).
#   personal_recall / audit_chain_verify / delegate / llm_think /
#     text_summarize / operator_profile_read: no constraints.
#
# Posture: GREEN per ADR-0086 Decision 1 — read-only catalog
# discipline is non-acting; provenance discipline enforced by
# constitution policy regardless of posture.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0086 Phase A — Birth Librarian-D1"
echo "=========================================================="

echo
echo "[1/4] Restarting daemon to load librarian role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

echo
echo "[2/4] Checking for existing Librarian-D1"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Librarian-D1']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Librarian-D1 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Librarian-D1 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "librarian",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Librarian-D1",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 120 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: birth-librarian-d1" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    exit 2
  fi
  echo "      Librarian-D1 born: instance_id=${INSTANCE_ID}"
fi

mkdir -p "$(pwd)/data/knowledge"
mkdir -p "$(pwd)/data/knowledge/catalog"

echo
echo "[3/4] Patching Librarian-D1's constitution with per-tool constraints"
CONST_PATH=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)

if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
  echo "      Skipping constraint patch — Librarian-D1 will run with"
  echo "      guardian-genre defaults until manually patched."
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
            "data/knowledge/",
        ]
        constraints["forbidden_paths"] = [
            "src/",
            "config/",
            "data/registry.sqlite",
            ".env",
            "~/.fsf/secrets",
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
echo "[4/4] Setting Librarian-D1's posture to GREEN"
POSTURE_RESP=$(curl -s --max-time 10 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: posture-librarian-d1-init" \
  -d '{"posture": "green", "reason": "ADR-0086 Decision 1 — librarian read-only catalog discipline is non-acting; provenance + topic-tag requirements enforced by constitution policy regardless of posture."}' 2>&1)
echo "      Posture response (truncated):"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "Librarian-D1 ready. Summary:"
echo "  instance_id:   ${INSTANCE_ID}"
echo "  role:          librarian"
echo "  genre:         guardian"
echo "  posture:       green"
echo "  catalog root:  data/knowledge/catalog/ (created if absent)"
echo "  next steps:    birth Prospector-D1, then run an initial"
echo "                 knowledge_curation skill dispatch."
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1 || true
