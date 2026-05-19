#!/bin/bash
# Engineer-Main daily commit_changelog driver. Option B from the
# scheduled-autonomous-work survey.
#
# Daily 6am cadence (via dev.forest.engineer-changelog.plist).
# Reads the last 24h of git log + diff stat as the "diff" input,
# dispatches commit_changelog.v1 on Engineer-Main. The skill runs
# commit_message.v1 to draft a conventional-commit-shaped subject,
# then text_summarize.v1 to compress into a 3-bullet user-facing
# changelog entry. Output lands in Engineer-Main's lineage memory.
#
# Useful for: operator wakes up, reads "what landed yesterday"
# digest, decides whether to nudge anything. The bullets trace
# back to real commits so there's no LLM invention.

set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
ENV_FILE="$REPO_ROOT/.env"
TOKEN=""
[ -f "$ENV_FILE" ] && TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "Engineer-Main daily changelog"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================================="

# Self-heal installed skill (gitignored).
SKILL_DST="$REPO_ROOT/data/forge/skills/installed/commit_changelog.v1.yaml"
if [ ! -f "$SKILL_DST" ] && [ -f "$REPO_ROOT/examples/skills/commit_changelog.v1.yaml" ]; then
  cp "$REPO_ROOT/examples/skills/commit_changelog.v1.yaml" "$SKILL_DST"
fi

resolve_id() {
  curl -s --max-time 5 "${DAEMON}/agents?limit=300" -H "X-FSF-Token: $TOKEN" 2>/dev/null \
    | python3 -c "import sys,json; data=json.load(sys.stdin); ids=[a.get('instance_id') for a in data.get('agents',[]) if a.get('agent_name')=='$1' and a.get('status')=='active']; print(ids[0] if ids else '')" 2>/dev/null
}

echo
echo "[1/3] Resolving Engineer-Main"
ENG_ID=$(resolve_id "Engineer-Main")
[ -z "$ENG_ID" ] && { echo "ERROR: Engineer-Main not found. Run birth-triune-main.command."; exit 2; }
echo "      Engineer-Main: $ENG_ID"

echo
echo "[2/3] Building diff for last 24h"
# `git log --since` returns commits in the window; we pair with
# `git show --stat --no-prefix` per commit so the LLM sees both
# the messages and the size of each change. Keep size bounded;
# input to commit_changelog is the diff field which is descriptive
# text, not literal patch bytes.
DIFF=$(cd "$REPO_ROOT" && git log --since='24 hours ago' --pretty=format:'== %h %s%n%b' --shortstat 2>/dev/null | head -c 30000)
if [ -z "$DIFF" ]; then
  echo "      No commits in last 24h. Nothing to summarize. Exiting clean."
  exit 0
fi
echo "      diff length: ${#DIFF} chars"

echo
echo "[3/3] Dispatching commit_changelog.v1 on Engineer-Main"
PAYLOAD=$(python3 <<PYEOF
import json, uuid
payload = {
    "skill_name": "commit_changelog",
    "skill_version": "1",
    "session_id": f"daily-changelog-{uuid.uuid4()}",
    "inputs": {
        "diff": """$DIFF""",
        "scope_hint": "",
        "audience": "operators",
    },
}
print(json.dumps(payload))
PYEOF
)

RESP=$(curl -s --max-time 180 "${DAEMON}/agents/${ENG_ID}/skills/run" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1)
echo "      Skill response (truncated):"
echo "$RESP" | python3 -m json.tool 2>/dev/null | head -50 || echo "$RESP" | head -50

STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
if [ "$STATUS" != "succeeded" ]; then
  echo "ERROR: commit_changelog returned status=$STATUS"
  exit 4
fi

echo
echo "=========================================================="
echo "Daily changelog complete — see Engineer-Main lineage memory"
echo "=========================================================="
exit 0
