#!/bin/bash
# ADR-0077 T2b — birth MigrationPilot-D4 (migration_pilot role).
#
# Per-tool constraint patches (ADR-0077 §migration_pilot kit table):
#   code_edit:  allowed_paths=["src/forest_soul_forge/registry/",
#               "tests/migrations/"]
#   shell_exec: allowed_commands=["sqlite3","pytest","python3"]
#
# Posture: yellow. The apply-step approval gate is constitutional
# (require_human_approval_for_apply policy) — yellow posture adds
# a redundant gate during the bedding-in phase.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0077 T2b — Birth MigrationPilot-D4 (migration_pilot)"
echo "=========================================================="

# ---------------------------------------------------------------------------
# 1. Restart daemon.
# ---------------------------------------------------------------------------
echo
echo "[1/4] Restarting daemon to load migration_pilot role"
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
echo "[2/4] Checking for existing MigrationPilot-D4"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='MigrationPilot-D4']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      MigrationPilot-D4 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing MigrationPilot-D4 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "migration_pilot",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "MigrationPilot-D4",
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
  echo "      MigrationPilot-D4 born: instance_id=${INSTANCE_ID}"
fi

# ---------------------------------------------------------------------------
# 3. Patch constitution with per-tool constraints.
# ---------------------------------------------------------------------------
echo
echo "[3/4] Patching MigrationPilot-D4's constitution with per-tool constraints"
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
    if name == "code_edit":
        # Migration files live in registry/. Test scaffolding for
        # the dry-run rehearsal lives in tests/migrations/.
        constraints["allowed_paths"] = [
            "src/forest_soul_forge/registry/",
            "tests/migrations/",
        ]
        constraints["forbidden_paths"] = [
            "examples/audit_chain.jsonl",
            "data/registry.sqlite",   # writes go through safe_migration.v1
            "config/",
        ]
        patched = True
    elif name == "shell_exec":
        constraints["allowed_commands"] = [
            "sqlite3", "pytest", "python", "python3",
        ]
        constraints["forbidden_commands"] = [
            "git push", "rm -rf", "dd", "mkfs", "sudo",
            "pip install",
        ]
        patched = True
if patched:
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print("      Constraints patched: code_edit, shell_exec")
else:
    print("      (no matching tools found for constraint patch)")
PY
fi

# ---------------------------------------------------------------------------
# 4. Set posture YELLOW.
# ---------------------------------------------------------------------------
echo
echo "[4/4] Setting MigrationPilot-D4's posture to YELLOW"
POSTURE_RESP=$(curl -s --max-time 5 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"posture": "yellow", "reason": "ADR-0077 default — apply step has constitutional approval gate; yellow posture adds bedding-in friction on dry-runs"}' 2>&1)
echo "      Posture response:"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "MigrationPilot-D4 ready. Summary:"
echo "  instance_id:    ${INSTANCE_ID}"
echo "  role:           migration_pilot"
echo "  genre:          guardian"
echo "  posture:        yellow"
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1
