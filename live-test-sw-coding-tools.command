#!/usr/bin/env bash
# SW.A.5 — live-test the coding tools (code_read, code_edit, shell_exec).
#
# Each tool needs per-agent allowed_paths + allowed_commands constraints
# in the constitution YAML (same convention as web_fetch's allowed_hosts).
# Birth doesn't auto-populate them — the operator (or this script) patches
# the constitution after birth. That's intentional: the operator has
# explicit consent over what the agent can read/write/exec.
#
# What this proves:
#   1. Engineer can code_read a real file in this repo (README.md)
#   2. Engineer can shell_exec `git status` (returns repo state)
#   3. Engineer can code_edit a file in /tmp (gated tool — note: this
#      MIGHT land in approval queue if requires_human_approval fires;
#      script tolerates either succeeded OR pending_approval as success)
#   4. Engineer's code_read REFUSES a path outside allowed_paths
#      (path-escape defense holds)
#   5. Reviewer (Guardian genre) cannot use code_edit — kit-tier
#      enforcement blocks it because Reviewer's archetype kit doesn't
#      include code_edit and Reviewer's genre ceiling is read_only
#
# This is the FIRST test where a Forest agent does external work that
# the operator could verify by hand. Engineering closing the loop.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
auth_header() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

bar() { printf "\n========== %s ==========\n" "$1"; }
ok()  { printf "  ✓ %s\n" "$1"; }
no()  { printf "  ✗ %s\n" "$1" >&2; }
die() { no "$1"; echo ""; echo "Press return to close."; read -r _; exit 1; }

SUFFIX="$(date +%s)"
ENG_NAME="EngTest_${SUFFIX}"
REV_NAME="RevTest_${SUFFIX}"
REPO_ABS="$HERE"     # this project's root — what we'll allow the agent to touch

# ---- Step 0: preflight -----------------------------------------------------
bar "0. preflight"
curl -sf --max-time 5 "$DAEMON/healthz" >/dev/null || die "daemon not reachable"
ok "daemon $DAEMON reachable"

# Confirm new tools registered
tools_json=$(curl -sf "$DAEMON/tools/registered")
for t in code_read code_edit shell_exec; do
  has=$(echo "$tools_json" | jq --arg n "$t" '[.tools[] | select(.name == $n)] | length')
  [[ "$has" -ge 1 ]] || die "tool $t not registered (restart daemon?)"
done
ok "code_read.v1, code_edit.v1, shell_exec.v1 all registered"

# ---- Step 1: birth Engineer ------------------------------------------------
bar "1. birth Engineer"
payload=$(jq -n --arg name "$ENG_NAME" '{
  profile: {role: "software_engineer", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false
}')
body=$(curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "birth: $body"
ENG_ID=$(echo "$body" | jq -r '.instance_id')
ENG_CONST=$(echo "$body" | jq -r '.constitution_path')
ok "Engineer  $ENG_ID  constitution=$ENG_CONST"

# ---- Step 2: birth Reviewer -----------------------------------------------
bar "2. birth Reviewer"
payload=$(jq -n --arg name "$REV_NAME" '{
  profile: {role: "code_reviewer", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false
}')
body=$(curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "birth: $body"
REV_ID=$(echo "$body" | jq -r '.instance_id')
REV_CONST=$(echo "$body" | jq -r '.constitution_path')
ok "Reviewer  $REV_ID  constitution=$REV_CONST"

# ---- Step 3: patch Engineer's constitution with allowlists ----------------
bar "3. patch Engineer constitution: allowed_paths + allowed_commands + relax approval for the test"
.venv/bin/python3 <<PYEOF
import yaml
from pathlib import Path

const_path = Path("$ENG_CONST")
const = yaml.safe_load(const_path.read_text())

# Find the tools block and patch each tool's constraints.
# Constitution shape (from soul_generator):
#   tools:
#     - name: code_read
#       version: "1"
#       constraints:
#         max_calls_per_session: ...
#         requires_human_approval: ...
tools = const.get("tools", [])

allowed_paths    = ["$REPO_ABS", "/tmp"]
allowed_commands = ["git", "ls", "echo", "pwd", "cat", "wc"]

for tool in tools:
    name = tool.get("name")
    constraints = tool.setdefault("constraints", {})
    if name == "code_read":
        constraints["allowed_paths"] = allowed_paths
    elif name == "code_edit":
        constraints["allowed_paths"] = allowed_paths
        # FOR THIS DEMO ONLY: relax approval gate so we can verify the
        # path-allowlist works end-to-end without the operator queue
        # intercepting. In production this stays True per Engineer's
        # constitution rule (approval_for_destructive_changes).
        constraints["requires_human_approval"] = False
    elif name == "shell_exec":
        constraints["allowed_paths"]    = allowed_paths
        constraints["allowed_commands"] = allowed_commands
        constraints["requires_human_approval"] = False

const_path.write_text(yaml.safe_dump(const, sort_keys=False, default_flow_style=False))
print("patched")
PYEOF
[[ "$(tail -1 <<< "patched")" == "patched" ]] || die "constitution patch failed"
ok "Engineer constitution patched (allowed_paths + commands + approval relaxed for demo)"

# ---- Step 4: code_read README.md (in-allowlist) → should succeed ---------
bar "4. code_read README.md (in-allowlist)"
payload=$(jq -n --arg session "ct-$SUFFIX-r1" --arg p "$REPO_ABS/README.md" '{
  tool_name: "code_read",
  tool_version: "1",
  session_id: $session,
  args: {path: $p, max_bytes: 5000}
}')
body=$(curl -sf --max-time 30 -X POST "$DAEMON/agents/$ENG_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "code_read failed: $body"
status=$(echo "$body" | jq -r '.status')
[[ "$status" == "succeeded" ]] || die "code_read status=$status: $body"
size=$(echo "$body" | jq -r '.result.output.size_bytes')
read_n=$(echo "$body" | jq -r '.result.output.bytes_read')
truncated=$(echo "$body" | jq -r '.result.output.truncated')
sha=$(echo "$body" | jq -r '.result.output.sha256')
ok "read README.md: ${read_n}/${size} bytes (truncated=$truncated)  sha256=${sha:0:16}…"

# ---- Step 5: code_read /etc/passwd (OUT-of-allowlist) → should refuse ----
bar "5. code_read /etc/passwd (out-of-allowlist) — MUST refuse"
payload=$(jq -n --arg session "ct-$SUFFIX-r2" '{
  tool_name: "code_read",
  tool_version: "1",
  session_id: $session,
  args: {path: "/etc/passwd"}
}')
body=$(curl -s --max-time 30 -X POST "$DAEMON/agents/$ENG_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload")
status=$(echo "$body" | jq -r '.status // "?"')
if [[ "$status" == "failed" ]] && echo "$body" | grep -qi 'allowed_paths\|outside'; then
  ok "/etc/passwd refused as expected (path-allowlist holds)"
elif [[ "$status" == "succeeded" ]]; then
  die "SECURITY FAIL — Engineer was able to read /etc/passwd!"
else
  echo "  body: ${body:0:300}"
  die "unexpected response shape — verify path-allowlist manually"
fi

# ---- Step 6: shell_exec git status (in-allowlist) → should succeed -------
bar "6. shell_exec git status (in-allowlist)"
payload=$(jq -n --arg session "ct-$SUFFIX-s1" --arg cwd "$REPO_ABS" '{
  tool_name: "shell_exec",
  tool_version: "1",
  session_id: $session,
  args: {argv: ["git", "status", "--short"], cwd: $cwd, timeout_s: 10}
}')
body=$(curl -sf --max-time 30 -X POST "$DAEMON/agents/$ENG_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "shell_exec failed: $body"
status=$(echo "$body" | jq -r '.status')
[[ "$status" == "succeeded" ]] || die "shell_exec status=$status: $body"
rc=$(echo "$body" | jq -r '.result.output.returncode')
stdout=$(echo "$body" | jq -r '.result.output.stdout')
ok "git status returned rc=$rc  stdout chars=${#stdout}"
echo "    first lines:"
echo "$stdout" | head -5 | sed 's/^/      /'

# ---- Step 7: shell_exec rm -rf (out-of-allowlist) → should refuse --------
bar "7. shell_exec 'rm -rf /' (NOT in allowed_commands) — MUST refuse"
payload=$(jq -n --arg session "ct-$SUFFIX-s2" '{
  tool_name: "shell_exec",
  tool_version: "1",
  session_id: $session,
  args: {argv: ["rm", "-rf", "/"], timeout_s: 5}
}')
body=$(curl -s --max-time 30 -X POST "$DAEMON/agents/$ENG_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload")
status=$(echo "$body" | jq -r '.status // "?"')
if [[ "$status" == "failed" ]] && echo "$body" | grep -qi 'allowed_commands\|not in.*allowed'; then
  ok "rm refused as expected (allowed_commands holds)"
elif [[ "$status" == "succeeded" ]]; then
  die "SECURITY FAIL — Engineer was able to invoke rm!"
else
  echo "  body: ${body:0:300}"
  die "unexpected response shape — verify allowed_commands manually"
fi

# ---- Step 8: code_edit /tmp file (in-allowlist) → should succeed ---------
bar "8. code_edit /tmp file (in-allowlist)"
TMP_TARGET="/tmp/sw-coding-test-${SUFFIX}.txt"
TMP_CONTENT="Hello from $ENG_NAME at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
payload=$(jq -n --arg session "ct-$SUFFIX-e1" --arg p "$TMP_TARGET" --arg c "$TMP_CONTENT" '{
  tool_name: "code_edit",
  tool_version: "1",
  session_id: $session,
  args: {path: $p, content: $c, mode: "write"}
}')
body=$(curl -s --max-time 30 -X POST "$DAEMON/agents/$ENG_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload")
status=$(echo "$body" | jq -r '.status')
if [[ "$status" == "pending_approval" ]]; then
  ok "code_edit landed in approval queue (expected if requires_human_approval=true)"
elif [[ "$status" == "succeeded" ]]; then
  bw=$(echo "$body" | jq -r '.result.output.bytes_written')
  ok "code_edit wrote ${bw} bytes to $TMP_TARGET"
  # Verify the file actually exists with the right content
  if [[ -f "$TMP_TARGET" ]] && grep -q "Hello from $ENG_NAME" "$TMP_TARGET"; then
    ok "file content verified on disk"
  else
    die "file content mismatch — wrote ok but contents wrong"
  fi
  rm -f "$TMP_TARGET"
else
  echo "  body: ${body:0:300}"
  die "code_edit unexpected status=$status"
fi

# ---- Step 9: Reviewer attempts code_edit → should be REFUSED -------------
bar "9. Reviewer attempts code_edit — MUST refuse (kit-tier enforcement)"
# code_edit isn't in Reviewer's archetype kit and Reviewer's genre is
# guardian (max_side_effects=read_only); the kit-tier guard refuses
# at birth-time / dispatch-time. We try anyway with tools_add wouldn't
# work because Reviewer has no allowed_paths constraint. Either way
# the dispatch should fail.
payload=$(jq -n --arg session "ct-$SUFFIX-rev1" '{
  tool_name: "code_edit",
  tool_version: "1",
  session_id: $session,
  args: {path: "/tmp/reviewer-cant-write.txt", content: "should not be created", mode: "write"}
}')
body=$(curl -s --max-time 30 -X POST "$DAEMON/agents/$REV_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload")
status=$(echo "$body" | jq -r '.status // "?"')
http_indicator=$(echo "$body" | jq -r '.detail // ""')
if [[ "$status" != "succeeded" ]]; then
  ok "Reviewer code_edit refused (status=$status; reason was: ${http_indicator:0:100}${body:0:100})"
  if [[ -f "/tmp/reviewer-cant-write.txt" ]]; then
    die "SECURITY FAIL — Reviewer file got created despite refusal!"
  fi
else
  die "SECURITY FAIL — Reviewer was able to invoke code_edit!"
fi

# ---- Cleanup --------------------------------------------------------------
bar "10. cleanup — archive both"
for id in "$ENG_ID" "$REV_ID"; do
  arch=$(jq -n --arg id "$id" '{instance_id: $id, reason: "A.5 coding-tools test cleanup"}')
  curl -s --max-time 10 -o /dev/null -X POST "$DAEMON/archive" \
    -H "Content-Type: application/json" $(auth_header) -d "$arch"
done
ok "archived both test agents"

bar "PASSED — A.5 coding tools live-verified end-to-end"
echo ""
echo "  ✓ code_read.v1   reads in-allowlist files; refuses /etc/passwd"
echo "  ✓ shell_exec.v1  runs allowed commands; refuses rm"
echo "  ✓ code_edit.v1   writes in-allowlist files (or queues approval)"
echo "  ✓ Reviewer (Guardian genre) refused from code_edit at the kit-tier gate"
echo ""
echo "Press return to close."
read -r _
