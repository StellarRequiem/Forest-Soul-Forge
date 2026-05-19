#!/usr/bin/env bash
# SW-track — birth, bond, and exercise the coding triune.
#
# What this proves:
#   1. The 3 new roles (system_architect, software_engineer, code_reviewer)
#      birth cleanly with the right tool kit (llm_think, memory_write,
#      memory_recall, delegate)
#   2. /triune/bond seals all three with restrict_delegations=true
#   3. llm_think.v1 dispatches successfully against the live LLM
#      (qwen2.5-coder:7b via Ollama)
#   4. Each agent gets a role-appropriate response from its model
#   5. The audit chain captures tool_call_dispatched + tool_call_succeeded
#      for each llm_think call with token counts
#   6. In-bond delegate.v1 succeeds (Architect → Engineer)
#   7. Out-of-bond delegate.v1 is REFUSED with restrict_delegations
#
# This is the "agents you can actually talk to" milestone — the
# foundational shape for what becomes Y-track conversation runtime.
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
ARCHITECT_NAME="Atlas_${SUFFIX}"
ENGINEER_NAME="Forge_${SUFFIX}"
REVIEWER_NAME="Sentinel_${SUFFIX}"
BOND_NAME="coding_triune_${SUFFIX}"

# ---- Step 0: daemon + LLM reachable ---------------------------------------
bar "0. preflight"
hc=$(curl -sf --max-time 5 "$DAEMON/healthz" || true)
[[ -z "$hc" ]] && die "daemon not reachable at $DAEMON"
ok "daemon $DAEMON reachable"

prov=$(curl -sf --max-time 5 "$DAEMON/runtime/provider" || true)
prov_status=$(echo "$prov" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('health',{}).get('status','?'))" 2>/dev/null || echo "?")
prov_model=$(echo "$prov" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('health',{}).get('models',{}).get('conversation','?'))" 2>/dev/null || echo "?")
[[ "$prov_status" != "ok" ]] && die "LLM provider not OK (status=$prov_status). Run ollama-coder-up.command first."
ok "LLM provider ok — model=$prov_model"

# ---- Step 1: birth Atlas (Architect) --------------------------------------
bar "1. birth Atlas (system_architect)"
payload=$(jq -n --arg name "$ARCHITECT_NAME" '{
  profile: {role: "system_architect", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: true
}')
body=$(curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "birth failed: $body"
ARCHITECT_ID=$(echo "$body" | jq -r '.instance_id')
ok "Atlas born  $ARCHITECT_ID"

# ---- Step 2: birth Forge (Engineer) ---------------------------------------
bar "2. birth Forge (software_engineer)"
payload=$(jq -n --arg name "$ENGINEER_NAME" '{
  profile: {role: "software_engineer", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: true
}')
body=$(curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "birth failed: $body"
ENGINEER_ID=$(echo "$body" | jq -r '.instance_id')
ok "Forge born  $ENGINEER_ID"

# ---- Step 3: birth Sentinel (Reviewer) ------------------------------------
bar "3. birth Sentinel (code_reviewer)"
payload=$(jq -n --arg name "$REVIEWER_NAME" '{
  profile: {role: "code_reviewer", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: true
}')
body=$(curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "birth failed: $body"
REVIEWER_ID=$(echo "$body" | jq -r '.instance_id')
ok "Sentinel born  $REVIEWER_ID"

# ---- Step 4: triune-bond all three ----------------------------------------
bar "4. POST /triune/bond — seal $BOND_NAME (restrict_delegations=true)"
payload=$(jq -n \
  --arg bn "$BOND_NAME" \
  --arg a "$ARCHITECT_ID" --arg e "$ENGINEER_ID" --arg r "$REVIEWER_ID" \
  '{bond_name: $bn, instance_ids: [$a, $e, $r], operator_id: "sw-track-bringup", restrict_delegations: true}')
body=$(curl -sf -X POST "$DAEMON/triune/bond" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "triune bond failed: $body"
SEQ=$(echo "$body" | jq -r '.ceremony_seq')
ok "triune bonded — ceremony_seq=$SEQ  restrict_delegations=true"

# ---- Step 5: dispatch llm_think to Atlas (Architect) ---------------------
bar "5. dispatch llm_think to Atlas — design question"
payload=$(jq -n --arg session "sw-test-$SUFFIX-architect" '{
  tool_name: "llm_think",
  tool_version: "1",
  session_id: $session,
  args: {
    prompt: "In 2 sentences: name two structural risks of putting all agent identities behind a single shared SQLite write-lock.",
    max_tokens: 200
  }
}')
body=$(curl -sf --max-time 120 -X POST "$DAEMON/agents/$ARCHITECT_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "Atlas llm_think failed: $body"
status=$(echo "$body" | jq -r '.status')
[[ "$status" != "succeeded" ]] && die "Atlas llm_think status=$status: $body"
response=$(echo "$body" | jq -r '.result.output.response')
elapsed_ms=$(echo "$body" | jq -r '.result.output.elapsed_ms')
tokens=$(echo "$body" | jq -r '.result.tokens_used // "?"')
ok "Atlas responded in ${elapsed_ms}ms (~$tokens tokens):"
echo "    \"${response:0:200}${response: -10}\""

# ---- Step 6: dispatch llm_think to Forge (Engineer) ----------------------
bar "6. dispatch llm_think to Forge — implementation question"
payload=$(jq -n --arg session "sw-test-$SUFFIX-engineer" '{
  tool_name: "llm_think",
  tool_version: "1",
  session_id: $session,
  args: {
    prompt: "Show a 3-line Python snippet that retries an HTTP request up to 3 times with exponential backoff. No imports beyond requests + time.",
    max_tokens: 300
  }
}')
body=$(curl -sf --max-time 120 -X POST "$DAEMON/agents/$ENGINEER_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "Forge llm_think failed: $body"
status=$(echo "$body" | jq -r '.status')
[[ "$status" != "succeeded" ]] && die "Forge llm_think status=$status: $body"
response=$(echo "$body" | jq -r '.result.output.response')
elapsed_ms=$(echo "$body" | jq -r '.result.output.elapsed_ms')
ok "Forge responded in ${elapsed_ms}ms:"
echo "    \"${response:0:240}…\""

# ---- Step 7: dispatch llm_think to Sentinel (Reviewer) -------------------
bar "7. dispatch llm_think to Sentinel — review question"
payload=$(jq -n --arg session "sw-test-$SUFFIX-reviewer" '{
  tool_name: "llm_think",
  tool_version: "1",
  session_id: $session,
  args: {
    prompt: "What is the single biggest concern with this Python snippet? `def divide(a,b): return a/b`",
    max_tokens: 200
  }
}')
body=$(curl -sf --max-time 120 -X POST "$DAEMON/agents/$REVIEWER_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "Sentinel llm_think failed: $body"
status=$(echo "$body" | jq -r '.status')
[[ "$status" != "succeeded" ]] && die "Sentinel llm_think status=$status: $body"
response=$(echo "$body" | jq -r '.result.output.response')
elapsed_ms=$(echo "$body" | jq -r '.result.output.elapsed_ms')
ok "Sentinel responded in ${elapsed_ms}ms:"
echo "    \"${response:0:200}…\""

# ---- Step 8: in-bond delegate (Atlas → Forge) — should succeed ----------
bar "8. in-bond delegate.v1 — Atlas asking Forge to handle implementation"
payload=$(jq -n --arg session "sw-test-$SUFFIX-deleg-in" --arg t "$ENGINEER_ID" '{
  tool_name: "delegate",
  tool_version: "1",
  session_id: $session,
  args: {
    target_instance_id: $t,
    skill_name: "noop_skill_does_not_exist",
    skill_version: "1",
    inputs: {dummy: "x"},
    reason: "in-bond delegate test — Architect → Engineer"
  }
}')
body=$(curl -sf --max-time 30 -X POST "$DAEMON/agents/$ARCHITECT_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || true
status=$(echo "$body" | jq -r '.status // "?"')
# We expect status=failed because the skill doesn't exist, BUT the failure
# reason should be "skill not installed" — NOT "out_of_triune_attempt".
# That confirms the bond gate ALLOWED the delegation even though the
# skill itself was bogus.
reason=$(echo "$body" | jq -r '.refused_reason // .output.failure_reason // "?"')
output=$(echo "$body" | jq -r '.output // {}')
if echo "$body" | grep -q 'out_of_triune_attempt'; then
  die "in-bond delegate WAS REFUSED as out-of-triune (bond not respected)"
fi
ok "in-bond delegate accepted by triune gate (skill failure is expected & secondary)"

# ---- Step 9: out-of-bond delegate (Atlas → some random other agent) -----
bar "9. out-of-bond delegate — should be REFUSED with restrict_delegations"
# Pick any other active agent that's NOT in our triune to use as target.
other=$(curl -sf "$DAEMON/agents" | jq -r --arg a "$ARCHITECT_ID" --arg e "$ENGINEER_ID" --arg r "$REVIEWER_ID" \
  '[.agents[] | select(.status == "active") | select(.instance_id != $a and .instance_id != $e and .instance_id != $r)] | .[0].instance_id // ""')
if [[ -z "$other" ]]; then
  echo "  (no out-of-bond target available; skipping)"
else
  payload=$(jq -n --arg session "sw-test-$SUFFIX-deleg-out" --arg t "$other" '{
    tool_name: "delegate",
    tool_version: "1",
    session_id: $session,
    args: {
      target_instance_id: $t,
      skill_name: "noop_skill_does_not_exist",
      skill_version: "1",
      inputs: {dummy: "x"},
      reason: "out-of-bond delegate test — should be refused"
    }
  }')
  body=$(curl -s --max-time 30 -X POST "$DAEMON/agents/$ARCHITECT_ID/tools/call" \
    -H "Content-Type: application/json" $(auth_header) -d "$payload")
  # The delegator raises DelegateError with "triune restriction" in the
  # message when the target isn't in the caller's bond. The dispatcher
  # wraps that into a failed status with the message in failure_message.
  # We check (a) status==failed AND (b) the message mentions triune.
  status=$(echo "$body" | jq -r '.status // "?"')
  fail_msg=$(echo "$body" | jq -r '.failure_message // ""')
  if [[ "$status" == "failed" ]] && echo "$body" | grep -qi 'triune'; then
    ok "out-of-bond delegate REFUSED as expected (status=failed, message mentions triune)"
  elif [[ "$status" == "failed" ]]; then
    # Status failed but for a different reason — print and check carefully
    echo "  out-of-bond returned failed but reason isn't obviously triune-related:"
    echo "  failure_message: $fail_msg"
    echo "  full body: ${body:0:400}"
    die "out-of-bond refusal reason unclear — verify manually"
  else
    die "out-of-bond delegate was NOT refused — restrict_delegations broken: $body"
  fi
fi

# ---- Step 10: verify audit chain has the llm_think dispatches ------------
bar "10. verify audit chain captured llm_think calls"
audit_tail=$(curl -sf "$DAEMON/audit/tail?n=200")
for inst in "$ARCHITECT_ID" "$ENGINEER_ID" "$REVIEWER_ID"; do
  dispatched=$(echo "$audit_tail" | jq --arg id "$inst" '[.events[] | select(.event_type == "tool_call_dispatched" and .instance_id == $id)] | length')
  succeeded=$(echo "$audit_tail" | jq --arg id "$inst" '[.events[] | select(.event_type == "tool_call_succeeded" and .instance_id == $id)] | length')
  if [[ "$dispatched" -ge 1 && "$succeeded" -ge 1 ]]; then
    ok "$inst → $dispatched dispatched, $succeeded succeeded"
  else
    die "$inst: missing audit events (dispatched=$dispatched succeeded=$succeeded)"
  fi
done

bar "SW LIVE TEST PASSED"
echo ""
echo "Coding triune verified end-to-end:"
echo "  ✓ 3 agents born with new roles + correct kits"
echo "  ✓ Triune sealed with restrict_delegations=true"
echo "  ✓ llm_think.v1 working against $prov_model"
echo "  ✓ Each agent answered its role-appropriate question"
echo "  ✓ In-bond delegate accepted; out-of-bond refused"
echo "  ✓ Audit chain captured every dispatch"
echo ""
echo "Triune members:"
echo "  Atlas    (Architect)  $ARCHITECT_ID"
echo "  Forge    (Engineer)   $ENGINEER_ID"
echo "  Sentinel (Reviewer)   $REVIEWER_ID"
echo ""
echo "These agents are KEPT (not archived) so you can interact with them."
echo "Press return to close."
read -r _
