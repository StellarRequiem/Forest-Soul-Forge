#!/bin/bash
# ADR-0056 E1 — birth Smith (the Experimenter agent).
#
# Runs in 5 phases:
#
#   1. Restart the daemon so it picks up the new `experimenter`
#      role definition added to trait_tree.yaml + genres.yaml +
#      constitution_templates.yaml + tool_catalog.yaml.
#   2. POST /birth with role=experimenter, agent_name=Smith.
#      The daemon writes Smith's constitution.yaml + soul.md to
#      the artifacts directory and emits the agent_created
#      audit event.
#   3. Patch Smith's constitution.yaml on disk to add
#      branch-isolation constraints on shell_exec.v1 +
#      code_edit.v1 per ADR-0056 D3. The kernel's birth path
#      doesn't accept per-tool constraints in the request — they
#      live in the constitution and are applied post-birth via a
#      direct YAML patch, then verified by the next dispatch.
#   4. Set Smith's posture to YELLOW via the existing
#      POST /agents/{instance_id}/posture endpoint
#      (ADR-0045).
#   5. Provision the workspace clone at
#      ~/.fsf/experimenter-workspace/Forest-Soul-Forge/
#      via `git clone` of the current commit. Smith's
#      shell_exec runs from this directory, never the
#      operator's main work tree.
#
# Idempotent: re-runs detect Smith already exists and skip the
# birth POST; constitution patch is line-anchored so re-applying
# is a no-op; posture set is naturally idempotent; workspace
# clone skips if directory already exists.

set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0056 E1 — Birth Smith (Experimenter)"
echo "=========================================================="

# ---------------------------------------------------------------------------
# 1. Restart daemon to load new role definitions.
# ---------------------------------------------------------------------------
echo
echo "[1/5] Restarting daemon to load experimenter role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

# ---------------------------------------------------------------------------
# 2. Check whether Smith already exists; birth if not.
# ---------------------------------------------------------------------------
echo
echo "[2/5] Checking for existing Smith"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='Smith']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      Smith already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing Smith — issuing /birth POST"
  # Empty trait_values lets the kernel compute defaults from
  # the experimenter role's domain_weights (trait_tree.yaml).
  # The first birth attempt invented trait names that don't
  # exist in the trait registry — empty + role-driven defaults
  # is the right shape.
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "experimenter",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "Smith",
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
    echo "      Full response above. Likely cause: role validation"
    echo "      rejected the trait_values, or the daemon hasn't picked"
    echo "      up the new experimenter role yet."
    exit 2
  fi
  echo "      Smith born: instance_id=${INSTANCE_ID}"
fi

# ---------------------------------------------------------------------------
# 3. Patch constitution with branch-isolation constraints.
# ---------------------------------------------------------------------------
echo
echo "[3/5] Patching Smith's constitution with branch-isolation constraints"
ARTIFACTS_DIR="$(grep -E '^FSF_ARTIFACTS_DIR=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo 'examples')"
if [ -z "$ARTIFACTS_DIR" ]; then
  ARTIFACTS_DIR="examples"
fi

# Constitution path is artifacts_dir / soul / agent_name-instance.constitution.yaml
# but the exact filename depends on the registry. Look it up via the
# /agents endpoint.
CONST_PATH=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)

if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
  echo "      Branch-isolation constraints will need manual patching."
  echo "      Continuing — Smith births fine without them but"
  echo "      shell_exec safety relies on them landing before any"
  echo "      work-mode dispatch."
else
  echo "      Constitution at: $CONST_PATH"
  # The tool-constraint patch: append per-tool constraints under
  # the existing tools[].constraints blocks. We use a Python
  # one-liner to do this safely (YAML is too sensitive to
  # sed-string-edits).
  #
  # Use the venv's Python which has PyYAML installed; system
  # python3 doesn't (first attempt 2026-05-07 hit
  # ModuleNotFoundError: No module named 'yaml').
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
        constraints["allowed_commands"] = [
            "git", "pytest", "ruff", "mypy", "semgrep", "bandit",
            "python", "python3", "pip", "fsf", "curl",
        ]
        constraints["forbidden_commands"] = [
            "rm -rf", "dd", "mkfs", "sudo",
            "git push origin main", "git push origin master",
        ]
        constraints["forbidden_paths"] = [
            "examples/audit_chain.jsonl",
            "data/registry.sqlite",
            "~/.fsf/secrets",
        ]
        patched = True
    elif name == "code_edit":
        constraints["allowed_paths"] = [
            "src/", "tests/", "docs/", "config/",
            "dev-tools/", "frontend/",
        ]
        constraints["forbidden_paths"] = [
            "examples/audit_chain.jsonl",
            "data/registry.sqlite",
        ]
        patched = True
    elif name == "web_fetch":
        constraints["allowed_hosts"] = [
            "docs.python.org", "github.com", "raw.githubusercontent.com",
            "arxiv.org", "docs.claude.com", "support.claude.com",
            "api.anthropic.com", "pypi.org", "files.pythonhosted.org",
        ]
        patched = True
if patched:
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print("      Constraints patched: shell_exec, code_edit, web_fetch")
else:
    print("      (no matching tools found for constraint patch — kit may be narrower than expected)")
PY
fi

# ---------------------------------------------------------------------------
# 4. Set posture YELLOW.
# ---------------------------------------------------------------------------
echo
echo "[4/5] Setting Smith's posture to YELLOW"
POSTURE_RESP=$(curl -s --max-time 5 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"posture": "yellow", "reason": "ADR-0056 E1 default — operator review per non-read-only dispatch"}' 2>&1)
echo "      Posture response:"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

# ---------------------------------------------------------------------------
# 5. Provision the experimenter workspace clone.
# ---------------------------------------------------------------------------
echo
echo "[5/5] Provisioning experimenter-workspace clone"
WORKSPACE_PARENT="$HOME/.fsf/experimenter-workspace"
WORKSPACE_DIR="$WORKSPACE_PARENT/Forest-Soul-Forge"

if [ -d "$WORKSPACE_DIR/.git" ]; then
  echo "      Workspace already exists: $WORKSPACE_DIR — skipping clone"
  echo "      (To reset: rm -rf $WORKSPACE_DIR + re-run this script)"
else
  mkdir -p "$WORKSPACE_PARENT"
  CURRENT_REPO="$(pwd)"
  CURRENT_HEAD=$(git -C "$CURRENT_REPO" rev-parse HEAD)
  echo "      Cloning $CURRENT_REPO -> $WORKSPACE_DIR @ $CURRENT_HEAD"
  git clone --quiet "$CURRENT_REPO" "$WORKSPACE_DIR"
  cd "$WORKSPACE_DIR"
  git config user.name "Smith (Experimenter)"
  git config user.email "smith@experimenter.local"
  # First cycle branch — Smith's work-mode dispatches operate on
  # branches like experimenter/cycle-N off main.
  git branch experimenter/cycle-1
  cd - >/dev/null
  echo "      Workspace ready. First-cycle branch experimenter/cycle-1 created."
fi

# ---------------------------------------------------------------------------
# Verify.
# ---------------------------------------------------------------------------
echo
echo "=========================================================="
echo "Smith ready. Summary:"
echo "  instance_id:    ${INSTANCE_ID}"
echo "  role:           experimenter"
echo "  genre:          actuator"
echo "  posture:        yellow"
echo "  workspace:      ${WORKSPACE_DIR}"
echo "  first branch:   experimenter/cycle-1"
echo "=========================================================="
echo
echo "Next tranches:"
echo "  E2 (B188) — ModeKitClampStep + task_caps.mode plumbing"
echo "  E3 (B189) — explore-mode scheduled tasks"
echo "  E4 (B190) — display-mode chat-tab pane"
echo
echo "Press any key to close this window."
read -n 1
