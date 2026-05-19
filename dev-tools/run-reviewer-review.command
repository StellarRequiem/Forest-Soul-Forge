#!/bin/bash
# Reviewer-Main weekly code_review_quick driver. Option C from the
# scheduled-autonomous-work survey.
#
# Weekly Monday 8am cadence (via dev.forest.reviewer-review.plist).
# Picks the most-recent file changed in the last 7 days of commits,
# dispatches code_review_quick.v1 on Reviewer-Main against that file.
# The skill reads the file, runs code_explain, then llm_think with a
# critic prompt. Output lands in Reviewer-Main's lineage memory.
#
# Useful for: weekly "what did the reviewer notice in our recent
# work" pattern surface. Not a substitute for human review;
# complementary signal.

set -uo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
ENV_FILE="$REPO_ROOT/.env"
TOKEN=""
[ -f "$ENV_FILE" ] && TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "Reviewer-Main weekly code review"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================================="

# Self-heal installed skill (gitignored).
SKILL_DST="$REPO_ROOT/data/forge/skills/installed/code_review_quick.v1.yaml"
if [ ! -f "$SKILL_DST" ] && [ -f "$REPO_ROOT/examples/skills/code_review_quick.v1.yaml" ]; then
  cp "$REPO_ROOT/examples/skills/code_review_quick.v1.yaml" "$SKILL_DST"
fi

resolve_id() {
  curl -s --max-time 5 "${DAEMON}/agents?limit=300" -H "X-FSF-Token: $TOKEN" 2>/dev/null \
    | python3 -c "import sys,json; data=json.load(sys.stdin); ids=[a.get('instance_id') for a in data.get('agents',[]) if a.get('agent_name')=='$1' and a.get('status')=='active']; print(ids[0] if ids else '')" 2>/dev/null
}

echo
echo "[1/3] Resolving Reviewer-Main"
REV_ID=$(resolve_id "Reviewer-Main")
[ -z "$REV_ID" ] && { echo "ERROR: Reviewer-Main not found. Run birth-triune-main.command."; exit 2; }
echo "      Reviewer-Main: $REV_ID"

echo
echo "[2/3] Picking most-recent Python file changed in last 7 days"
# Prefer .py because code_explain.v1's enums prefer concrete
# languages. Filter out generated files + tests so review focuses
# on production code.
TARGET_PATH=""
while IFS= read -r p; do
  # Skip tests, vendored, .venv, __pycache__, etc.
  case "$p" in
    tests/*|*/tests/*|*/.venv/*|*/__pycache__/*|*.bak|examples/audit_chain.jsonl)
      continue ;;
  esac
  if [ -f "$REPO_ROOT/$p" ]; then
    TARGET_PATH="$REPO_ROOT/$p"
    break
  fi
done < <(cd "$REPO_ROOT" && git log --since='7 days ago' --name-only --pretty='' -- '*.py' 2>/dev/null | sort -u)

if [ -z "$TARGET_PATH" ]; then
  echo "      No production .py file changed in last 7 days. Exiting clean."
  exit 0
fi
echo "      target: $TARGET_PATH"

echo
echo "[3/3] Dispatching code_review_quick.v1 on Reviewer-Main"
PAYLOAD=$(python3 <<PYEOF
import json, uuid
payload = {
    "skill_name": "code_review_quick",
    "skill_version": "1",
    "session_id": f"weekly-review-{uuid.uuid4()}",
    "inputs": {
        "file_path": "$TARGET_PATH",
        "language": "python",
    },
}
print(json.dumps(payload))
PYEOF
)

RESP=$(curl -s --max-time 180 "${DAEMON}/agents/${REV_ID}/skills/run" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1)
echo "      Skill response (truncated):"
echo "$RESP" | python3 -m json.tool 2>/dev/null | head -50 || echo "$RESP" | head -50

STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
if [ "$STATUS" != "succeeded" ]; then
  echo "ERROR: code_review_quick returned status=$STATUS"
  exit 4
fi

echo
echo "=========================================================="
echo "Weekly review complete — see Reviewer-Main lineage memory"
echo "=========================================================="
exit 0
