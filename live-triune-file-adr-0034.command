#!/usr/bin/env bash
# SW.B.2 — first real triune task: file ADR-0034 (the SW-track ADR).
#
# This is the threshold task per ADR-0034 itself: the Architect/Engineer/
# Reviewer triune participates in filing the ADR that defines them.
# Meta-demo, but honest meta-demo — the audit chain captures every step
# of the read+review chain.
#
# What this proves:
#   1. Triune births cleanly with the post-A.5 archetype kits
#      (Atlas + Forge + Sentinel each get llm_think + code_read + the
#      role-appropriate writes/refusals)
#   2. /triune/bond seals all three with restrict_delegations=true
#   3. Each agent can code_read.v1 the ADR file under their patched
#      allowed_paths
#   4. Each agent's llm_think.v1 produces a role-appropriate response
#      using the live LLM (qwen2.5-coder:7b via Ollama or whatever's
#      configured)
#   5. The audit chain captures the full chain: 3 births + 1 ceremony +
#      3 constitution patches + N code_reads + 3 llm_thinks
#
# Output the operator can review:
#   - Atlas: design assessment ("does this ADR's design hold?")
#   - Forge: implementation note ("is the design implementable?")
#   - Sentinel: review verdict ("APPROVED / REJECTED + rationale")
#
# Operator (you) decides whether to keep the ADR as-is, edit per
# Sentinel's review, or roll it back. The triune doesn't merge; you do.

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
ARCHITECT_NAME="Atlas_${SUFFIX}"
ENGINEER_NAME="Forge_${SUFFIX}"
REVIEWER_NAME="Sentinel_${SUFFIX}"
BOND_NAME="b2_adr_filing_${SUFFIX}"
ADR_PATH="$HERE/docs/decisions/ADR-0034-software-engineering-track.md"
ADR_0033_PATH="$HERE/docs/decisions/ADR-0033-security-swarm.md"

# ---- Step 0: preflight ----------------------------------------------------
bar "0. preflight"
hc=$(curl -sf --max-time 5 "$DAEMON/healthz" || true)
[[ -z "$hc" ]] && die "daemon not reachable at $DAEMON"
ok "daemon $DAEMON reachable"

prov=$(curl -sf --max-time 5 "$DAEMON/runtime/provider" || true)
prov_status=$(echo "$prov" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('health',{}).get('status','?'))" 2>/dev/null || echo "?")
prov_model=$(echo "$prov" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('health',{}).get('models',{}).get('conversation','?'))" 2>/dev/null || echo "?")
[[ "$prov_status" != "ok" ]] && die "LLM provider not OK (status=$prov_status). Run ollama-coder-up.command first."
ok "LLM provider ok — model=$prov_model"

[[ -f "$ADR_PATH" ]] || die "ADR-0034 file missing — expected at $ADR_PATH"
[[ -f "$ADR_0033_PATH" ]] || die "ADR-0033 file missing — expected at $ADR_0033_PATH"
ok "ADR-0034 (target) and ADR-0033 (reference) both exist on disk"

# ---- Step 1-3: birth Atlas, Forge, Sentinel -------------------------------
birth_agent() {
  local role="$1" name="$2"
  local payload
  payload=$(jq -n --arg name "$name" --arg role "$role" '{
    profile: {role: $role, trait_values: {}, domain_weight_overrides: {}},
    agent_name: $name,
    agent_version: "v1",
    enrich_narrative: false
  }')
  curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1
}

bar "1. birth Atlas (system_architect)"
body=$(birth_agent "system_architect" "$ARCHITECT_NAME") || die "Atlas birth: $body"
ARCHITECT_ID=$(echo "$body" | jq -r '.instance_id')
ARCHITECT_CONST=$(echo "$body" | jq -r '.constitution_path')
ok "Atlas born $ARCHITECT_ID"

bar "2. birth Forge (software_engineer)"
body=$(birth_agent "software_engineer" "$ENGINEER_NAME") || die "Forge birth: $body"
ENGINEER_ID=$(echo "$body" | jq -r '.instance_id')
ENGINEER_CONST=$(echo "$body" | jq -r '.constitution_path')
ok "Forge born $ENGINEER_ID"

bar "3. birth Sentinel (code_reviewer)"
body=$(birth_agent "code_reviewer" "$REVIEWER_NAME") || die "Sentinel birth: $body"
REVIEWER_ID=$(echo "$body" | jq -r '.instance_id')
REVIEWER_CONST=$(echo "$body" | jq -r '.constitution_path')
ok "Sentinel born $REVIEWER_ID"

# ---- Step 4: bond into a triune -------------------------------------------
bar "4. /triune/bond — seal coding triune"
payload=$(jq -n \
  --arg name "$BOND_NAME" \
  --arg a "$ARCHITECT_ID" \
  --arg e "$ENGINEER_ID" \
  --arg r "$REVIEWER_ID" \
  --arg op "live-triune-file-adr-0034" \
  '{bond_name: $name, instance_ids: [$a, $e, $r], operator_id: $op, restrict_delegations: true}')
body=$(curl -sf --max-time 30 -X POST "$DAEMON/triune/bond" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) || die "triune/bond: $body"
ok "triune $BOND_NAME bonded (restrict_delegations=true)"

# ---- Step 5: patch all three constitutions with allowed_paths -------------
# Each agent needs to be able to code_read.v1 files under docs/decisions/.
bar "5. patch constitutions: allowed_paths for code_read"
.venv/bin/python3 <<PYEOF
import yaml
from pathlib import Path

repo_root = "$HERE"
allowed_paths = [repo_root]

for const_path_str in ["$ARCHITECT_CONST", "$ENGINEER_CONST", "$REVIEWER_CONST"]:
    cp = Path(const_path_str)
    const = yaml.safe_load(cp.read_text())
    for tool in const.get("tools", []):
        if tool.get("name") in ("code_read", "code_edit", "shell_exec"):
            tool.setdefault("constraints", {})["allowed_paths"] = allowed_paths
    cp.write_text(yaml.safe_dump(const, sort_keys=False, default_flow_style=False))
    print(f"patched {cp.name}")
PYEOF
ok "constitutions patched (allowed_paths = repo root)"

# ---- Step 6: Atlas reads ADR-0034 + ADR-0033, then llm_thinks design ------
read_file_via() {
  local agent_id="$1" path="$2" session="$3"
  local payload
  payload=$(jq -n --arg p "$path" --arg s "$session" '{
    tool_name: "code_read",
    tool_version: "1",
    session_id: $s,
    args: {path: $p, max_bytes: 60000}
  }')
  curl -sf --max-time 30 -X POST "$DAEMON/agents/$agent_id/tools/call" \
    -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1
}

llm_think_via() {
  local agent_id="$1" prompt="$2" session="$3"
  local payload
  payload=$(jq -n --arg p "$prompt" --arg s "$session" '{
    tool_name: "llm_think",
    tool_version: "1",
    session_id: $s,
    args: {prompt: $p, task_kind: "conversation", max_tokens: 400}
  }')
  curl -sf --max-time 90 -X POST "$DAEMON/agents/$agent_id/tools/call" \
    -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1
}

bar "6. Atlas — design assessment"
SESS_A="b2-atlas-${SUFFIX}"
body=$(read_file_via "$ARCHITECT_ID" "$ADR_PATH" "$SESS_A") || die "Atlas code_read ADR-0034: $body"
adr_text=$(echo "$body" | jq -r '.result.output.content // ""')
ok "Atlas read ADR-0034 ($(echo "$adr_text" | wc -c | tr -d ' ') chars)"

prompt="You are Atlas, a system architect agent. You designed this codebase
along with the operator. Read the ADR-0034 below and produce a TERSE design
assessment (3-5 bullet points): does the design hold? Is the structural
pattern (3 roles claiming 3 existing genres) sound vs. the precedent set by
ADR-0033? What's missing or over-stated?

ADR-0034 content (first 18000 chars):
${adr_text:0:18000}"
body=$(llm_think_via "$ARCHITECT_ID" "$prompt" "$SESS_A") || die "Atlas llm_think: $body"
status=$(echo "$body" | jq -r '.status')
[[ "$status" != "succeeded" ]] && die "Atlas llm_think status=$status: ${body:0:300}"
atlas_response=$(echo "$body" | jq -r '.result.output.response // ""')
atlas_tokens=$(echo "$body" | jq -r '.result.tokens_used // 0')
section "ATLAS — design assessment ($atlas_tokens tokens)"
echo "$atlas_response"

# ---- Step 7: Forge — implementation note ----------------------------------
bar "7. Forge — implementation note"
SESS_F="b2-forge-${SUFFIX}"
body=$(read_file_via "$ENGINEER_ID" "$ADR_PATH" "$SESS_F") || die "Forge code_read: $body"
ok "Forge re-read ADR-0034 (own session)"

prompt="You are Forge, the software engineer agent. You implement what
Atlas designs. Read the ADR-0034 phases section and answer TERSELY (3-5
bullet points): is each phase implementable as described? Are the tool
allowlists honest about what shipped? Anything that needs a tranche split
or de-scope?

ADR-0034 phases section (first 18000 chars of full ADR):
${adr_text:0:18000}"
sleep 3   # Give Ollama room between back-to-back calls
body=$(llm_think_via "$ENGINEER_ID" "$prompt" "$SESS_F") || die "Forge llm_think: $body"
status=$(echo "$body" | jq -r '.status')
[[ "$status" != "succeeded" ]] && die "Forge llm_think status=$status: ${body:0:300}"
forge_response=$(echo "$body" | jq -r '.result.output.response // ""')
forge_tokens=$(echo "$body" | jq -r '.result.tokens_used // 0')
section "FORGE — implementation note ($forge_tokens tokens)"
echo "$forge_response"

# ---- Step 8: Sentinel — review verdict ------------------------------------
bar "8. Sentinel — review verdict"
SESS_S="b2-sentinel-${SUFFIX}"

# Sentinel reads ADR-0034 first, then ADR-0033 for comparison
body=$(read_file_via "$REVIEWER_ID" "$ADR_PATH" "$SESS_S") || die "Sentinel code_read 0034: $body"
ok "Sentinel read ADR-0034"
body=$(read_file_via "$REVIEWER_ID" "$ADR_0033_PATH" "$SESS_S") || die "Sentinel code_read 0033: $body"
adr_0033_text=$(echo "$body" | jq -r '.result.output.content // ""' | head -c 15000)
ok "Sentinel read ADR-0033 (precedent, ${#adr_0033_text} chars truncated)"

prompt="You are Sentinel, the code reviewer agent. Genre = guardian, you
do not implement, you review and refuse or approve. Two ADRs follow.
Compare ADR-0034 (under review) against ADR-0033 (the precedent for
domain-tier expansion). Produce a TERSE review verdict in this exact
format:

VERDICT: APPROVED | APPROVED_WITH_CHANGES | REJECTED
RATIONALE: <2-4 sentences>
REQUIRED_CHANGES: <bulleted list, or 'none'>
PRECEDENT_ALIGNMENT: <1-2 sentences on whether 0034 follows 0033's structural pattern>

ADR-0034 (under review, first 12000 chars):
${adr_text:0:12000}

ADR-0033 (precedent, first 8000 chars):
${adr_0033_text:0:8000}"
sleep 3
body=$(llm_think_via "$REVIEWER_ID" "$prompt" "$SESS_S") || die "Sentinel llm_think: $body"
status=$(echo "$body" | jq -r '.status')
[[ "$status" != "succeeded" ]] && die "Sentinel llm_think status=$status: ${body:0:300}"
sentinel_response=$(echo "$body" | jq -r '.result.output.response // ""')
sentinel_tokens=$(echo "$body" | jq -r '.result.tokens_used // 0')
section "SENTINEL — review verdict ($sentinel_tokens tokens)"
echo "$sentinel_response"

# ---- Step 9: surface audit chain summary ---------------------------------
bar "9. audit chain — last 25 events"
audit=$(curl -sf "$DAEMON/audit/tail?n=25" 2>&1) || die "audit tail: $audit"
echo "$audit" | python3 -c "
import json, sys
d = json.load(sys.stdin)
events = d.get('events', [])
for ev in events:
    seq = ev.get('seq')
    et = ev.get('event_type')
    dna = ev.get('agent_dna', '-') or '-'
    data = ev.get('event_data', {}) or {}
    extra = ''
    if et == 'tool_call_succeeded':
        extra = f\"  tool={data.get('tool_key')}  tokens={data.get('tokens_used','-')}\"
    elif et == 'agent_created':
        extra = f\"  name={data.get('agent_name','?')}\"
    elif et == 'ceremony':
        extra = f\"  bond={data.get('bond_name','?')}  agents={len(data.get('instance_ids',[]))}\"
    print(f'  seq={seq:>4}  {et:<28}  dna={dna:<14}{extra}')
"

# ---- Step 10: cleanup -----------------------------------------------------
bar "10. cleanup — archive triune"
for id in "$ARCHITECT_ID" "$ENGINEER_ID" "$REVIEWER_ID"; do
  arch=$(jq -n --arg id "$id" '{instance_id: $id, reason: "B.2 ADR-0034 filing exercise complete"}')
  curl -s --max-time 10 -o /dev/null -X POST "$DAEMON/archive" \
    -H "Content-Type: application/json" $(auth_header) -d "$arch"
done
ok "archived all three"

bar "DONE — B.2 first triune task complete"
echo ""
echo "  Triune participated in filing ADR-0034. Audit chain captured the chain."
echo "  Operator decides next: keep the ADR as-is, edit per Sentinel's verdict,"
echo "  or roll it back."
echo ""
echo "Press return to close."
read -r _
