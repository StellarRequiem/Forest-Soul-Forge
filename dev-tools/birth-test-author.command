#!/bin/bash
# ADR-0077 T2b — birth TestAuthor-D4 (test_author role).
#
# Mirrors birth-smith.command's 5-phase shape: daemon restart →
# birth POST → constitution patch (per-tool constraints) →
# posture set → summary. Idempotent: re-runs skip the birth if
# TestAuthor-D4 already exists.
#
# Per-tool constraint patches (ADR-0077 §test_author kit table):
#   code_edit:  allowed_paths=["tests/"], forbidden_paths=["src/"]
#   shell_exec: allowed_commands=["pytest","python3"], forbidden ops
#               include git/pip/rm-rf
#   web_fetch:  allowed_hosts limited to framework docs
#
# Posture: yellow (every non-read-only dispatch queues for
# operator approval until trust is established).

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0077 T2b — Birth TestAuthor-D4 (test_author)"
echo "=========================================================="

# ---------------------------------------------------------------------------
# 1. Restart daemon so it picks up the new test_author role.
# ---------------------------------------------------------------------------
echo
echo "[1/4] Restarting daemon to load test_author role"
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
echo "[2/4] Checking for existing TestAuthor-D4"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='TestAuthor-D4']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      TestAuthor-D4 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing TestAuthor-D4 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "test_author",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "TestAuthor-D4",
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
    echo "      Likely cause: trait_engine hasn't picked up the new role"
    echo "      (kickstart timing) or constitution_templates.yaml has a"
    echo "      typo blocking the role_base resolution. Check daemon logs."
    exit 2
  fi
  echo "      TestAuthor-D4 born: instance_id=${INSTANCE_ID}"
fi

# ---------------------------------------------------------------------------
# 3. Patch constitution with per-tool constraints.
# ---------------------------------------------------------------------------
echo
echo "[3/4] Patching TestAuthor-D4's constitution with per-tool constraints"
CONST_PATH=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)

if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
  echo "      Skipping constraint patch — TestAuthor-D4 will run with the"
  echo "      researcher-genre defaults until manually patched."
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
        # The forbid_production_code_edit policy is the load-bearing
        # invariant. Constraints enforce it at the tool layer too —
        # allowed_paths is a positive list, forbidden_paths is a hard
        # block over the top.
        constraints["allowed_paths"] = ["tests/"]
        constraints["forbidden_paths"] = [
            "src/",
            "examples/audit_chain.jsonl",
            "data/registry.sqlite",
            "config/",
        ]
        patched = True
    elif name == "shell_exec":
        constraints["allowed_commands"] = [
            "pytest", "python", "python3",
        ]
        constraints["forbidden_commands"] = [
            "git", "pip", "rm -rf", "dd", "mkfs", "sudo",
            "curl", "wget",
        ]
        constraints["forbidden_paths"] = [
            "src/", "examples/audit_chain.jsonl",
            "data/registry.sqlite", "~/.fsf/secrets",
        ]
        patched = True
    elif name == "web_fetch":
        constraints["allowed_hosts"] = [
            "docs.python.org", "docs.pytest.org",
            "github.com", "raw.githubusercontent.com",
        ]
        patched = True
if patched:
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print("      Constraints patched: code_edit, shell_exec, web_fetch")
else:
    print("      (no matching tools found for constraint patch — kit may be narrower than expected)")
PY
fi

# ---------------------------------------------------------------------------
# 4. Set posture YELLOW.
# ---------------------------------------------------------------------------
echo
echo "[4/4] Setting TestAuthor-D4's posture to YELLOW"
POSTURE_RESP=$(curl -s --max-time 5 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"posture": "yellow", "reason": "ADR-0077 default — operator review per non-read-only dispatch until trust is established"}' 2>&1)
echo "      Posture response:"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "TestAuthor-D4 ready. Summary:"
echo "  instance_id:    ${INSTANCE_ID}"
echo "  role:           test_author"
echo "  genre:          researcher"
echo "  posture:        yellow"
echo "  next steps:     birth-migration-pilot.command + birth-release-gatekeeper.command"
echo "=========================================================="
echo
echo "Press any key to close this window."
read -n 1
