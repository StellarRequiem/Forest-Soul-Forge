#!/usr/bin/env bash
# Forest Soul Forge — R4 registry parity smoke.
#
# Purpose: R4 is going to split registry/registry.py into per-table
# accessor classes + a Registry façade. The PUBLIC method surface
# (registry.register_birth, registry.get_agent, etc.) MUST stay
# identical so every router keeps working without edits. This script
# exercises the most common router→registry call paths end-to-end so
# any wiring regression surfaces immediately.
#
# What it covers (and which Registry method each step exercises):
#   1.  POST /birth                     → register_birth, next_sibling_index,
#                                          _insert_agent_row, _insert_ancestry_for,
#                                          _insert_audit_row, register_audit_event
#   2.  POST /spawn                     → all of the above + parent linkage
#   3.  GET  /agents                    → list_agents
#   4.  GET  /agents/{id}               → get_agent
#   5.  GET  /agents/by-dna/{dna}       → get_agent_by_dna
#   6.  GET  /agents/{id}/ancestors     → get_ancestors
#   7.  GET  /agents/{id}/descendants   → get_descendants
#   8.  GET  /audit/agent/{id}          → audit_for_agent
#   9.  GET  /audit/by-dna/{dna}        → audit_for_agent (variant)
#   10. POST /birth with same Idempotency-Key → lookup_idempotency_key,
#                                                 store_idempotency_key
#   11. POST /archive                   → update_status (cleanup)
#
# Not covered here (out of router-driven path; covered by pytest):
#   set_secret/get_secret/list_secret_names/delete_secret
#   record_tool_call/aggregate_tool_calls (covered by live-test-t2-tier)
#   record_pending_approval/* (covered by live-test-k4)
#   rebuild_from_artifacts (covered by lifespan ingest path)
#   schema_version, _verify_schema_version, _migrate_forward
#     (these only run at boot — daemon boot itself is the test)
#
# Each run uses NAME_<unix-ts>; multiple runs accumulate by design.
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

# ---- Step 0 ----
bar "0. daemon reachable"
curl -sf "$DAEMON/healthz" >/dev/null || die "daemon not reachable at $DAEMON"
ok "$DAEMON healthy"

SUFFIX="$(date +%s)"
PARENT_NAME="R4Parent_$SUFFIX"
CHILD_NAME="R4Child_$SUFFIX"

# ---- Step 1: birth a parent ----
bar "1. POST /birth — register_birth"
parent_payload=$(jq -n --arg name "$PARENT_NAME" '{
  profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false,
  tools_add: [{name: "delegate", version: "1"}]
}')
parent_body=$(curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" \
  $(auth_header) -d "$parent_payload") || die "/birth failed"
PARENT_ID=$(echo "$parent_body" | jq -r '.instance_id')
PARENT_DNA_FULL=$(echo "$parent_body" | jq -r '.dna_full')
PARENT_DNA_SHORT="${PARENT_DNA_FULL:0:12}"
ok "parent born  $PARENT_ID  dna_short=$PARENT_DNA_SHORT"

# ---- Step 2: spawn a child ----
bar "2. POST /spawn — register_birth + parent linkage"
child_payload=$(jq -n --arg name "$CHILD_NAME" --arg pid "$PARENT_ID" '{
  profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false,
  tools_add: [{name: "delegate", version: "1"}],
  parent_instance_id: $pid
}')
child_body=$(curl -sf -X POST "$DAEMON/spawn" -H "Content-Type: application/json" \
  $(auth_header) -d "$child_payload") || die "/spawn failed"
CHILD_ID=$(echo "$child_body" | jq -r '.instance_id')
ok "child spawned  $CHILD_ID"

# ---- Step 3: list_agents ----
bar "3. GET /agents — list_agents"
agents_list=$(curl -sf "$DAEMON/agents") || die "GET /agents failed"
n=$(echo "$agents_list" | jq -r '.agents | length')
[[ "$n" -gt 0 ]] || die "GET /agents returned 0 agents — list_agents broken?"
# Verify both newly-created agents are in the list.
for id in "$PARENT_ID" "$CHILD_ID"; do
  found=$(echo "$agents_list" | jq --arg id "$id" '.agents | map(select(.instance_id == $id)) | length')
  [[ "$found" == "1" ]] || die "agent $id NOT in /agents list"
done
ok "list contains $n agents (incl. parent + child)"

# ---- Step 4: get_agent ----
bar "4. GET /agents/{id} — get_agent"
parent_get=$(curl -sf "$DAEMON/agents/$PARENT_ID") || die "GET /agents/$PARENT_ID failed"
got_name=$(echo "$parent_get" | jq -r '.agent_name')
[[ "$got_name" == "$PARENT_NAME" ]] || die "get_agent returned wrong name: $got_name"
ok "get_agent returned correct row"

# ---- Step 5: get_agent_by_dna ----
bar "5. GET /agents/by-dna/{dna} — get_agent_by_dna"
by_dna=$(curl -sf "$DAEMON/agents/by-dna/$PARENT_DNA_SHORT") || die "by-dna failed"
n=$(echo "$by_dna" | jq -r '.agents | length')
[[ "$n" -gt 0 ]] || die "by-dna returned 0 agents"
# Both parent + child have the same dna_short (network_watcher / operator_companion
# with the empty trait profile produces deterministic DNA), so 2+ entries is OK.
ok "by-dna returned $n agents matching $PARENT_DNA_SHORT"

# ---- Step 6: get_ancestors ----
bar "6. GET /agents/{id}/ancestors — get_ancestors"
anc=$(curl -sf "$DAEMON/agents/$CHILD_ID/ancestors") || die "ancestors failed"
n=$(echo "$anc" | jq -r '.agents | length')
[[ "$n" -ge 1 ]] || die "child has no ancestors? expected ≥1, got $n"
# Must include the parent.
parent_in_anc=$(echo "$anc" | jq --arg id "$PARENT_ID" '.agents | map(select(.instance_id == $id)) | length')
[[ "$parent_in_anc" == "1" ]] || die "parent $PARENT_ID NOT in child's ancestors"
ok "child's ancestors include parent ($n total)"

# ---- Step 7: get_descendants ----
bar "7. GET /agents/{id}/descendants — get_descendants"
desc=$(curl -sf "$DAEMON/agents/$PARENT_ID/descendants") || die "descendants failed"
n=$(echo "$desc" | jq -r '.agents | length')
[[ "$n" -ge 1 ]] || die "parent has no descendants? expected ≥1, got $n"
child_in_desc=$(echo "$desc" | jq --arg id "$CHILD_ID" '.agents | map(select(.instance_id == $id)) | length')
[[ "$child_in_desc" == "1" ]] || die "child $CHILD_ID NOT in parent's descendants"
ok "parent's descendants include child ($n total)"

# ---- Step 8: audit_for_agent (by instance_id) ----
bar "8. GET /audit/agent/{id} — audit_for_agent"
audit_p=$(curl -sf "$DAEMON/audit/agent/$PARENT_ID") || die "audit/agent/parent failed"
n=$(echo "$audit_p" | jq -r '.events | length')
[[ "$n" -ge 1 ]] || die "parent has no audit events? expected ≥1, got $n"
# At least one event should be agent_created.
created_count=$(echo "$audit_p" | jq '[.events[] | select(.event_type == "agent_created")] | length')
[[ "$created_count" -ge 1 ]] || die "no agent_created event for parent"
ok "parent's audit chain has $n entries (incl. agent_created)"

# ---- Step 9: audit_for_agent (by dna) ----
bar "9. GET /audit/by-dna/{dna} — audit_for_agent variant"
audit_dna=$(curl -sf "$DAEMON/audit/by-dna/$PARENT_DNA_SHORT") || die "audit/by-dna failed"
n=$(echo "$audit_dna" | jq -r '.events | length')
[[ "$n" -ge 1 ]] || die "by-dna audit returned 0 events"
ok "by-dna audit returned $n entries"

# ---- Step 10: idempotency replay ----
# Header name: x-idempotency-key (NOT Idempotency-Key — see
# daemon/idempotency.py IDEMPOTENCY_HEADER). Discovered the hard way
# while writing this script — first version used Idempotency-Key,
# the daemon silently treated it as no-key, and the second POST
# created a fresh agent. Saved here so the next person doesn't
# re-discover.
bar "10. POST /birth twice w/ same x-idempotency-key — lookup/store_idempotency_key"
IDEM_KEY="r4-smoke-$SUFFIX"
IDEM_NAME="R4Idem_$SUFFIX"
idem_payload=$(jq -n --arg name "$IDEM_NAME" '{
  profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false,
  tools_add: [{name: "delegate", version: "1"}]
}')
first=$(curl -sf -X POST "$DAEMON/birth" \
  -H "Content-Type: application/json" \
  -H "x-idempotency-key: $IDEM_KEY" \
  $(auth_header) -d "$idem_payload") || die "first idempotent /birth failed"
FIRST_ID=$(echo "$first" | jq -r '.instance_id')
ok "first POST  → $FIRST_ID  (registry.store_idempotency_key landed cache row)"

second=$(curl -sf -X POST "$DAEMON/birth" \
  -H "Content-Type: application/json" \
  -H "x-idempotency-key: $IDEM_KEY" \
  $(auth_header) -d "$idem_payload") || die "second idempotent /birth failed"
SECOND_ID=$(echo "$second" | jq -r '.instance_id')

if [[ "$FIRST_ID" == "$SECOND_ID" ]]; then
  ok "second POST replayed cached response → same instance_id (registry.lookup_idempotency_key hit)"
else
  die "idempotency replay broken: first=$FIRST_ID second=$SECOND_ID — should be identical"
fi

# ---- Step 11: cleanup ----
bar "11. POST /archive — update_status (cleanup)"
for id in "$CHILD_ID" "$PARENT_ID" "$FIRST_ID"; do
  arch_payload=$(jq -n --arg id "$id" '{instance_id: $id, reason: "R4 smoke cleanup"}')
  hc=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$DAEMON/archive" \
    -H "Content-Type: application/json" $(auth_header) -d "$arch_payload")
  [[ "$hc" == "200" ]] || printf "  warn: archive %s returned %s\n" "$id" "$hc"
done
ok "archived parent + child + idem-test agent"

bar "PASSED"
echo "All 11 router→registry call paths verified."
echo "Re-run after R4 lands; identical green output means R4 didn't break"
echo "the registry public surface."
echo ""
echo "Press return to close."
read -r _
