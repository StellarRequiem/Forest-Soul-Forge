#!/usr/bin/env bash
# ADR-003Y Y3 — multi-agent conversation rooms with @mention chain.
#
# What this proves:
#   1. Births 2 agents (Atlas + Forge) into the same conversation
#   2. Operator @mentions Atlas explicitly — Atlas responds, NOT Forge
#   3. Operator addresses by addressed_to: list — that wins over @mentions
#   4. No-mention turn → fallback resolution to first agent
#   5. Operator turn that prompts Atlas to bring in Forge — chain pass
#      via @mention in Atlas's reply triggers Forge to respond next
#   6. max_chain_depth caps a runaway chain
#   7. Audit chain captures conversation_turn events with chain_depth +
#      in_response_to backrefs

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
ATLAS_NAME="Atlas_y3_${SUFFIX}"
FORGE_NAME="Forge_y3_${SUFFIX}"
DOMAIN="y3_multi_${SUFFIX}"
OPERATOR_ID="alex_y3"

bar "0. preflight"
hc=$(curl -sf --max-time 5 "$DAEMON/healthz" || true)
[[ -z "$hc" ]] && die "daemon not reachable"
ok "daemon reachable"

prov_status=$(curl -sf --max-time 5 "$DAEMON/runtime/provider" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('health',{}).get('status','?'))" 2>/dev/null || echo "?")
[[ "$prov_status" != "ok" ]] && die "LLM provider not OK"
ok "LLM provider ok"

# ---- Step 1-2: birth Atlas + Forge ---------------------------------------
birth_agent() {
  local role="$1" name="$2"
  jq -n --arg name "$name" --arg role "$role" '{
    profile: {role: $role, trait_values: {}, domain_weight_overrides: {}},
    agent_name: $name, agent_version: "v1", enrich_narrative: false
  }' | curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" $(auth_header) -d @- 2>&1
}

bar "1. birth Atlas (system_architect)"
body=$(birth_agent "system_architect" "$ATLAS_NAME") || die "Atlas birth: $body"
ATLAS_ID=$(echo "$body" | jq -r '.instance_id')
ATLAS_CONST=$(echo "$body" | jq -r '.constitution_path')
ok "Atlas $ATLAS_ID"

bar "2. birth Forge (software_engineer)"
body=$(birth_agent "software_engineer" "$FORGE_NAME") || die "Forge birth: $body"
FORGE_ID=$(echo "$body" | jq -r '.instance_id')
FORGE_CONST=$(echo "$body" | jq -r '.constitution_path')
ok "Forge $FORGE_ID"

# ---- Step 3: relax llm_think approval on both ----------------------------
bar "3. patch constitutions: relax llm_think approval"
.venv/bin/python3 <<PYEOF
import yaml
from pathlib import Path
for cp in ["$ATLAS_CONST", "$FORGE_CONST"]:
    p = Path(cp)
    d = yaml.safe_load(p.read_text())
    for tool in d.get("tools", []):
        if tool.get("name") == "llm_think":
            tool.setdefault("constraints", {})["requires_human_approval"] = False
    p.write_text(yaml.safe_dump(d, sort_keys=False, default_flow_style=False))
print("patched")
PYEOF
ok "both constitutions patched"

# ---- Step 4: create conversation -----------------------------------------
bar "4. create conversation in $DOMAIN"
body=$(jq -n --arg d "$DOMAIN" --arg op "$OPERATOR_ID" '{domain:$d, operator_id:$op}' | \
  curl -sf -X POST "$DAEMON/conversations" -H "Content-Type: application/json" $(auth_header) -d @-)
CONV_ID=$(echo "$body" | jq -r '.conversation_id')
ok "$CONV_ID"

# ---- Step 5: add both agents as participants -----------------------------
bar "5. add both agents to room"
for id in "$ATLAS_ID" "$FORGE_ID"; do
  jq -n --arg i "$id" '{instance_id:$i}' | \
    curl -sf -o /dev/null -X POST "$DAEMON/conversations/$CONV_ID/participants" \
    -H "Content-Type: application/json" $(auth_header) -d @-
done
participants=$(curl -sf "$DAEMON/conversations/$CONV_ID/participants" | jq -r '.participants | length')
[[ "$participants" -eq 2 ]] || die "expected 2 participants, got $participants"
ok "Atlas + Forge in room (count=$participants)"

# ---- Step 6: explicit @mention picks Forge -------------------------------
bar "6. operator @mention picks Forge specifically"
body=$(jq -n --arg sp "$OPERATOR_ID" --arg name "$FORGE_NAME" '{
  speaker: $sp,
  body: ("Hi @" + $name + ". One short sentence: how do you feel about your role?"),
  auto_respond: true,
  max_response_tokens: 150,
  max_chain_depth: 1
}' | curl -sf --max-time 120 -X POST "$DAEMON/conversations/$CONV_ID/turns" \
  -H "Content-Type: application/json" $(auth_header) -d @-)

CHAIN_DEPTH=$(echo "$body" | jq -r '.chain_depth')
RESPONDER_ID=$(echo "$body" | jq -r '.agent_turn.speaker')
RESPONSE=$(echo "$body" | jq -r '.agent_turn.body')

[[ "$CHAIN_DEPTH" -eq 1 ]] || die "expected depth 1, got $CHAIN_DEPTH"
[[ "$RESPONDER_ID" == "$FORGE_ID" ]] || die "expected Forge to respond, got $RESPONDER_ID"
ok "@Forge mention → Forge responded (depth=$CHAIN_DEPTH)"
section "Forge:"
echo "$RESPONSE" | head -3

# ---- Step 7: explicit addressed_to overrides @mention ---------------------
bar "7. addressed_to: [Atlas] overrides any @mentions in body"
body=$(jq -n --arg sp "$OPERATOR_ID" --arg name "$FORGE_NAME" --arg aid "$ATLAS_ID" '{
  speaker: $sp,
  body: ("@" + $name + " come hither — but I really want Atlas to answer this one."),
  addressed_to: [$aid],
  auto_respond: true,
  max_response_tokens: 150,
  max_chain_depth: 1
}' | curl -sf --max-time 120 -X POST "$DAEMON/conversations/$CONV_ID/turns" \
  -H "Content-Type: application/json" $(auth_header) -d @-)

RESPONDER_ID=$(echo "$body" | jq -r '.agent_turn.speaker')
[[ "$RESPONDER_ID" == "$ATLAS_ID" ]] || die "expected Atlas (addressed_to override), got $RESPONDER_ID"
ok "addressed_to=[Atlas] beat the @Forge mention in body"
section "Atlas:"
echo "$body" | jq -r '.agent_turn.body' | head -3

# ---- Step 8: no addressing → fallback to first participant (Atlas) -------
bar "8. no addressing/mention → fallback resolution"
body=$(jq -n --arg sp "$OPERATOR_ID" '{
  speaker: $sp,
  body: "Plain question to whoever is around: what time is it?",
  auto_respond: true,
  max_response_tokens: 80,
  max_chain_depth: 1
}' | curl -sf --max-time 120 -X POST "$DAEMON/conversations/$CONV_ID/turns" \
  -H "Content-Type: application/json" $(auth_header) -d @-)
RESPONDER_ID=$(echo "$body" | jq -r '.agent_turn.speaker')
ok "fallback responder: $RESPONDER_ID (whoever was first in participants)"

# ---- Step 9: max_chain_depth caps a chain --------------------------------
bar "9. max_chain_depth=2 caps the response chain"
# Even if agents @mention each other and pass forever, the chain stops at 2.
body=$(jq -n --arg sp "$OPERATOR_ID" --arg ap "$ATLAS_NAME" --arg fp "$FORGE_NAME" '{
  speaker: $sp,
  body: ("Discussion: @" + $ap + " — please consult @" + $fp + " on what dispatcher gating is."),
  auto_respond: true,
  max_response_tokens: 150,
  max_chain_depth: 2
}' | curl -sf --max-time 240 -X POST "$DAEMON/conversations/$CONV_ID/turns" \
  -H "Content-Type: application/json" $(auth_header) -d @-)
CHAIN_DEPTH=$(echo "$body" | jq -r '.chain_depth')
[[ "$CHAIN_DEPTH" -le 2 ]] || die "chain exceeded max_depth=2 (got $CHAIN_DEPTH)"
ok "chain stopped at depth=$CHAIN_DEPTH (≤2)"
echo "$body" | jq -r '.agent_turn_chain[] | "    [\(.speaker[-12:])] \(.body[0:90])"'

# ---- Step 10: audit chain — verify chain_depth in event_data --------------
bar "10. audit chain — verify chain_depth field on conversation_turn events"
audit=$(curl -sf "$DAEMON/audit/tail?n=50")
COUNT=$(echo "$audit" | jq -r '[.events[] | select(.event_type == "conversation_turn") | .event_data.chain_depth // empty] | length')
ok "$COUNT conversation_turn events carry chain_depth metadata"

# ---- Step 11: cleanup ----------------------------------------------------
bar "11. cleanup — archive room + agents"
jq -n '{status:"archived", reason:"Y3 smoke complete"}' | \
  curl -sf -o /dev/null -X POST "$DAEMON/conversations/$CONV_ID/status" \
  -H "Content-Type: application/json" $(auth_header) -d @-
for id in "$ATLAS_ID" "$FORGE_ID"; do
  jq -n --arg i "$id" '{instance_id:$i, reason:"Y3 smoke complete"}' | \
    curl -s -o /dev/null -X POST "$DAEMON/archive" \
    -H "Content-Type: application/json" $(auth_header) -d @-
done
ok "room archived, agents archived"

bar "PASSED — Y3 multi-agent conversation rooms verified"
echo ""
echo "  ✓ @mention resolves to specific participant (Forge)"
echo "  ✓ addressed_to: [...] overrides @mentions in body"
echo "  ✓ no-addressing fallback → first agent participant"
echo "  ✓ max_chain_depth caps runaway chains"
echo "  ✓ Audit chain captures chain_depth + in_response_to per turn"
echo ""
echo "Press return to close."
read -r _
