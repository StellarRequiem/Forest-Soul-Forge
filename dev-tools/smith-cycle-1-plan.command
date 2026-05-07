#!/bin/bash
# Cycle 1 dispatch — Smith plans his first work-mode cycle.
# Output gets saved to dev-tools/smith-cycle-1-plan-response.json
# AND echoed to this Terminal window.

set -uo pipefail
cd "$(dirname "$0")/.."

TOKEN=$(grep -E "^FSF_API_TOKEN=" .env | cut -d= -f2)
INSTANCE_ID="experimenter_1de20e0840a2"
SESSION_ID="smith-cycle-1-plan-v6-$(date +%Y%m%d)"
RESP_FILE="dev-tools/smith-cycle-1-plan-response-v6.json"
V3_SNAPSHOT="dev-tools/smith-cycle-1-v3-snapshot.py"

echo "=========================================================="
echo "Smith — cycle 1 plan dispatch"
echo "=========================================================="
echo "  agent:      $INSTANCE_ID"
echo "  session_id: $SESSION_ID"
echo "  mode:       work (ADR-0056 E2)"
echo "  provider:   local (qwen2.5-coder:7b — frontier per-agent"
echo "              routing not wired yet)"
echo "  timeout:    90s"
echo

BODY_FILE=$(mktemp)
# Build the JSON body via Python so the v3 snapshot can be inlined
# verbatim without manual escaping. The prompt embeds Smith's actual
# v3 file content under <prior_cycle> — testing whether iteration
# capability returns when prior state is in-context (per ADR-0056
# follow-up E7).
V3_SNAPSHOT_PATH="$V3_SNAPSHOT" "$(pwd)/.venv/bin/python3" - <<'PYEOF' > "$BODY_FILE"
import json, os, pathlib

snap = pathlib.Path(os.environ["V3_SNAPSHOT_PATH"]).read_text(encoding="utf-8")

prompt = (
    "You are Smith in WORK mode (ADR-0056). This is cycle 1.6. "
    "In v5 you preserved the v3 file structure perfectly (E7 finding "
    "validated) — but when you rewrote `_seed_conversation` you "
    "ignored the explicit kwargs in the prompt and invented "
    "`id=...` and `session_tag=...` instead. Neither parameter "
    "exists. This cycle tests whether literal copy-verbatim markers "
    "stop the paraphrasing.\n\n"
    "<prior_cycle>\n"
    + snap +
    "\n</prior_cycle>\n\n"
    "RULE FOR THIS CYCLE: the block below tagged "
    "<copy_verbatim id=\"helper\"> contains the EXACT replacement "
    "text for `_seed_conversation`'s body. You will copy this block "
    "into the file character-for-character. You will not rename "
    "any parameter. You will not paraphrase. You will not 'improve' "
    "the names. The only thing you may change is whitespace at the "
    "end of lines and the trailing newline. Any other deviation "
    "fails the cycle.\n\n"
    "<copy_verbatim id=\"helper\">\n"
    "def _seed_conversation(client: TestClient, conversation_id: str) -> None:\n"
    "    \"\"\"\n"
    "    Insert a conversation directly via registry on app.state so we don't need\n"
    "    write endpoints enabled. Falls back to POST if registry is accessible.\n"
    "    \"\"\"\n"
    "    registry = client.app.state.registry\n"
    "    registry.conversations.create_conversation(\n"
    "        domain=\"general\",\n"
    "        operator_id=\"test-operator\",\n"
    "        conversation_id=conversation_id,\n"
    "    )\n"
    "</copy_verbatim>\n\n"
    "Why these kwargs are correct: the actual signature of "
    "`registry.conversations.create_conversation` "
    "(src/forest_soul_forge/registry/tables/conversations.py line 89) "
    "is `(self, *, domain: str, operator_id: str, "
    "retention_policy='full_7d', when=None, conversation_id=None)`. "
    "There is NO `id`, NO `session_tag`, NO `agent_id`, NO `metadata`. "
    "Those names you reach for don't exist. The literal block above "
    "is the only set of kwargs that will not raise TypeError.\n\n"
    "REVISION SCOPE — single-helper replacement:\n"
    "- Replace the body of `_seed_conversation` in <prior_cycle> "
    "with the contents of <copy_verbatim id=\"helper\"> (excluding "
    "the tags themselves).\n"
    "- Every other line of the v3 file stays byte-for-byte identical. "
    "All imports, all comments, all docstrings, all four test "
    "methods, all blank lines, the unused `json` import, the "
    "_TOKEN/_AUTH constants, the _build_client + "
    "_append_shortcut_event helpers — unchanged.\n\n"
    "Output format:\n"
    "1. Confirmation: name the file path and the four test method "
    "names from <prior_cycle> (one sentence).\n"
    "2. Quote the <copy_verbatim id=\"helper\"> block back to me "
    "in a fenced python block, character-for-character. (This "
    "proves you read it.)\n"
    "3. Full revised file content, ready to apply.\n"
    "4. Verification (exact pytest command + 4 PASSED expected).\n"
    "5. Self-check: state the kwargs your `_seed_conversation` "
    "uses, verbatim, and confirm they match the <copy_verbatim> "
    "block. If they don't match, stop and report the mismatch "
    "instead of producing the file.\n\n"
    "Keep output under 2200 words."
)

body = {
    "tool_name": "llm_think",
    "tool_version": "1",
    "session_id": "smith-cycle-1-plan-v6",
    "args": {
        "prompt": prompt,
        "task_kind": "conversation",
        "max_tokens": 4000,
    },
    "task_caps": {
        "mode": "work",
        "usage_cap_tokens": 50000,
    },
}
print(json.dumps(body))
PYEOF

echo "POSTing dispatch (this may take 30-60s on the local model)..."
echo

RESP=$(curl -s --max-time 90 -X POST \
  "http://127.0.0.1:7423/agents/$INSTANCE_ID/tools/call" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d @"$BODY_FILE")
RC=$?
rm -f "$BODY_FILE"

if [ $RC -ne 0 ] || [ -z "$RESP" ]; then
  echo "ERROR: curl rc=$RC, response empty or timed out"
  echo
  echo "Press any key to close."
  read -n 1
  exit 1
fi

# Save raw response for the assistant to read.
echo "$RESP" > "$RESP_FILE"

echo "--- Smith's response ---"
echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
echo "------------------------"
echo
echo "Saved to: $RESP_FILE"
echo
echo "Press any key to close."
read -n 1
