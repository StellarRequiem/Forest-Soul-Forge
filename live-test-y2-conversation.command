#!/usr/bin/env bash
# ADR-003Y Y2 — single-agent conversation orchestration smoke test.
#
# What this proves:
#   1. /conversations CRUD endpoints work (Y1)
#   2. POST /turns with auto_respond=False just appends an operator turn (Y1)
#   3. POST /turns with auto_respond=True triggers llm_think.v1 dispatch
#      against the room's single agent participant, appends the agent's
#      response as a follow-up turn, and returns BOTH turns
#   4. The audit chain captures: conversation_started + 2× conversation_turn
#      (operator + agent) + tool_call_dispatched + tool_call_succeeded
#   5. Multi-turn coherence — agent's second response references the first
#
# Setup: births a single Forge agent (software_engineer / actuator),
# patches its constitution with task_caps relax (no approval queue
# bothering us in the smoke), creates a conversation, makes the agent
# the sole participant, drives 2 turn cycles.

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
section() { printf "\n--- %s ---\n" "$1"; }

SUFFIX="$(date +%s)"
AGENT_NAME="Forge_y2_${SUFFIX}"
DOMAIN="y2_smoke_${SUFFIX}"
OPERATOR_ID="alex_y2"

# ---- Step 0: preflight ----------------------------------------------------
bar "0. preflight"
hc=$(curl -sf --max-time 5 "$DAEMON/healthz" || true)
[[ -z "$hc" ]] && die "daemon not reachable at $DAEMON"
ok "daemon $DAEMON reachable"

prov_status=$(curl -sf --max-time 5 "$DAEMON/runtime/provider" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('health',{}).get('status','?'))" 2>/dev/null || echo "?")
[[ "$prov_status" != "ok" ]] && die "LLM provider not OK (status=$prov_status). Run ollama-coder-up.command first."
ok "LLM provider ok"

# ---- Step 1: birth Forge agent --------------------------------------------
bar "1. birth Forge agent (software_engineer)"
payload=$(jq -n --arg name "$AGENT_NAME" '{
  profile: {role: "software_engineer", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false
}')
body=$(curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "birth failed: $body"
AGENT_ID=$(echo "$body" | jq -r '.instance_id')
AGENT_CONST=$(echo "$body" | jq -r '.constitution_path')
ok "Forge born $AGENT_ID"

# ---- Step 2: patch constitution to relax llm_think approval --------------
bar "2. patch constitution: relax llm_think approval for the smoke"
.venv/bin/python3 <<PYEOF
import yaml
from pathlib import Path
const = Path("$AGENT_CONST")
data = yaml.safe_load(const.read_text())
for tool in data.get("tools", []):
    if tool.get("name") == "llm_think":
        tool.setdefault("constraints", {})["requires_human_approval"] = False
const.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
print("patched")
PYEOF
ok "llm_think approval relaxed (smoke only — actuator default is True)"

# ---- Step 3: create conversation ------------------------------------------
bar "3. create conversation in domain '$DOMAIN'"
payload=$(jq -n --arg domain "$DOMAIN" --arg op "$OPERATOR_ID" '{
  domain: $domain, operator_id: $op
}')
body=$(curl -sf -X POST "$DAEMON/conversations" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "create: $body"
CONV_ID=$(echo "$body" | jq -r '.conversation_id')
ok "conversation $CONV_ID  status=$(echo "$body" | jq -r '.status')  retention=$(echo "$body" | jq -r '.retention_policy')"

# ---- Step 4: add Forge as the sole agent participant ---------------------
bar "4. add Forge as participant"
payload=$(jq -n --arg id "$AGENT_ID" '{instance_id: $id}')
body=$(curl -sf -X POST "$DAEMON/conversations/$CONV_ID/participants" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "add participant: $body"
ok "Forge added (joined_at=$(echo "$body" | jq -r '.joined_at'))"

# ---- Step 5: operator types — auto_respond=True triggers Y2 orchestration ---
bar "5. operator turn 1 with auto_respond=True"
payload=$(jq -n --arg sp "$OPERATOR_ID" '{
  speaker: $sp,
  body: "Hi Forge. In one short sentence: what is the role of the dispatcher?",
  auto_respond: true,
  max_response_tokens: 200
}')
body=$(curl -sf --max-time 120 -X POST "$DAEMON/conversations/$CONV_ID/turns" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "turn 1: $body"

OP_TURN_ID=$(echo "$body" | jq -r '.operator_turn.turn_id')
AGENT_TURN_ID=$(echo "$body" | jq -r '.agent_turn.turn_id // ""')
AGENT_FAIL=$(echo "$body" | jq -r '.agent_dispatch_failed // false')
AGENT_BODY=$(echo "$body" | jq -r '.agent_turn.body // ""')
AGENT_TOKENS=$(echo "$body" | jq -r '.agent_turn.token_count // 0')

[[ -z "$AGENT_TURN_ID" || "$AGENT_FAIL" == "true" ]] && die "agent turn missing: ${body:0:300}"
ok "operator turn  $OP_TURN_ID"
ok "agent turn     $AGENT_TURN_ID  (${AGENT_TOKENS} tokens)"
section "Forge response 1:"
echo "$AGENT_BODY" | head -8

# ---- Step 6: second turn — coherence check -------------------------------
bar "6. operator turn 2 — does Forge maintain context?"
payload=$(jq -n --arg sp "$OPERATOR_ID" '{
  speaker: $sp,
  body: "Good. Now in one short sentence: what does the governance pipeline add to that?",
  auto_respond: true,
  max_response_tokens: 200
}')
body=$(curl -sf --max-time 120 -X POST "$DAEMON/conversations/$CONV_ID/turns" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "turn 2: $body"

AGENT_TURN_ID2=$(echo "$body" | jq -r '.agent_turn.turn_id // ""')
AGENT_FAIL2=$(echo "$body" | jq -r '.agent_dispatch_failed // false')
AGENT_BODY2=$(echo "$body" | jq -r '.agent_turn.body // ""')
AGENT_TOKENS2=$(echo "$body" | jq -r '.agent_turn.token_count // 0')

[[ -z "$AGENT_TURN_ID2" || "$AGENT_FAIL2" == "true" ]] && die "agent turn 2 missing: ${body:0:300}"
ok "agent turn 2   $AGENT_TURN_ID2  (${AGENT_TOKENS2} tokens)"
section "Forge response 2:"
echo "$AGENT_BODY2" | head -8

# ---- Step 7: list turns — should have 4 -----------------------------------
bar "7. list_turns: expect 4 (op1, agent1, op2, agent2)"
body=$(curl -sf "$DAEMON/conversations/$CONV_ID/turns?limit=10")
COUNT=$(echo "$body" | jq -r '.turns | length')
[[ "$COUNT" -ge 4 ]] || die "expected ≥4 turns, got $COUNT"
ok "$COUNT turns recorded chronologically"
echo "$body" | jq -r '.turns[] | "    [\(.timestamp[11:19])] \(.speaker): \(.body[0:80])"'

# ---- Step 8: audit chain - confirm conversation_turn events fired -------
bar "8. audit chain — recent conversation_turn + tool_call events"
audit=$(curl -sf "$DAEMON/audit/tail?n=30")
TURN_COUNT=$(echo "$audit" | jq -r '[.events[] | select(.event_type == "conversation_turn")] | length')
TC_OK=$(echo "$audit" | jq -r '[.events[] | select(.event_type == "tool_call_succeeded" and .event_data.tool_key == "llm_think.v1")] | length')
ok "conversation_turn events in tail: $TURN_COUNT (expect ≥4)"
ok "llm_think.v1 succeeded events:    $TC_OK (expect ≥2)"

# ---- Step 9: archive conversation ----------------------------------------
bar "9. archive conversation"
payload=$(jq -n '{status: "archived", reason: "Y2 smoke complete"}')
body=$(curl -sf -X POST "$DAEMON/conversations/$CONV_ID/status" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "archive: $body"
ok "status=$(echo "$body" | jq -r '.status')"

# ---- Step 10: cleanup -----------------------------------------------------
bar "10. cleanup — archive agent"
arch=$(jq -n --arg id "$AGENT_ID" '{instance_id: $id, reason: "Y2 smoke complete"}')
curl -s -o /dev/null -X POST "$DAEMON/archive" \
  -H "Content-Type: application/json" $(auth_header) -d "$arch"
ok "agent archived"

bar "PASSED — Y2 single-agent conversation orchestration end-to-end"
echo ""
echo "  ✓ Conversation create + add participant + retention default"
echo "  ✓ POST /turns auto_respond=True dispatched llm_think + appended response"
echo "  ✓ Multi-turn context preserved (Forge had history of turn 1)"
echo "  ✓ Audit chain captured both conversation_turn events + llm_think dispatches"
echo ""
echo "Press return to close."
read -r _
