#!/usr/bin/env bash
# Forest Soul Forge — R2 birth/spawn parity smoke.
#
# Purpose: this script captures the OBSERVABLE behavior of /birth and
# /spawn so the R2 refactor (extracting _perform_create from writes.py)
# can be diffed before vs after. If the after-refactor run differs in
# anything other than instance_ids, dna shorts, timestamps, and
# audit-chain seq numbers, R2 broke something.
#
# What it verifies:
#   1. POST /birth → 201, returns instance_id + dna_full + role
#   2. agent_created event landed in chain with the expected fields:
#        instance_id, agent_name, role, dna_full, sibling_index,
#        constitution_source, constitution_hash, soul_path,
#        constitution_path, owner_id, tools, tool_catalog_version,
#        genre  (parent_instance MUST be absent on /birth events)
#   3. POST /spawn → 201 with parent_instance_id pointed at #1
#   4. agent_spawned event landed with parent_instance + parent_dna +
#      lineage_depth fields populated
#   5. GET /agents/{id} returns both children rows
#   6. Cleanup: archive both
#
# Idempotency: each run uses NAME_<unix-ts>, so no name clashes across
# runs; the chain accumulates by design (matches the other live-test
# scripts).
#
# Outputs the full event JSON for both events to /tmp so the operator
# can save baseline.json and post-refactor.json and run jq diff.
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

# ---- Step 0: daemon reachable ------------------------------------------
bar "0. daemon reachable"
hc=$(curl -sf "$DAEMON/healthz" || true)
if [[ -z "$hc" ]]; then
  die "daemon not reachable at $DAEMON  (start run.command first)"
fi
ok "$DAEMON healthy"

SUFFIX="$(date +%s)"
PARENT_NAME="R2Parent_$SUFFIX"
CHILD_NAME="R2Child_$SUFFIX"
BASELINE_OUT="/tmp/r2-baseline-$SUFFIX.json"

# ---- Step 1: birth a parent --------------------------------------------
bar "1. POST /birth — parent agent"
parent_payload=$(jq -n --arg name "$PARENT_NAME" '{
  profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false,
  tools_add: [{name: "delegate", version: "1"}]
}')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST "$DAEMON/birth" \
  -H "Content-Type: application/json" $(auth_header) -d "$parent_payload")
parent_body="$(cat "$tmp")"; rm -f "$tmp"
if [[ "$http_code" != "201" && "$http_code" != "200" ]]; then
  die "/birth failed (http=$http_code): ${parent_body:0:400}"
fi
PARENT_ID=$(echo "$parent_body" | jq -r '.instance_id')
PARENT_DNA_FULL=$(echo "$parent_body" | jq -r '.dna_full')
# AgentRow.dna is the 12-char short form; the spawn event records
# parent_row.dna (short) as event_data.parent_dna. Computing it here so
# step 4's parent_dna assertion compares like-for-like.
PARENT_DNA_SHORT="${PARENT_DNA_FULL:0:12}"
ok "parent born  instance=$PARENT_ID  dna_short=$PARENT_DNA_SHORT  dna_full=${PARENT_DNA_FULL:0:16}…"

# ---- Step 2: agent_created event in chain ------------------------------
# Note on shape: /audit/tail returns AuditEventOut with these fields:
#   seq, timestamp, agent_dna, instance_id (lifted from event_data!),
#   event_type, event_json (the full payload, JSON-stringified),
#   entry_hash
# So the canonical filter is by top-level .instance_id, and inner
# field reads go through (.event_json | fromjson).
bar "2. agent_created event in chain"
audit_tail=$(curl -sf "$DAEMON/audit/tail?n=200")
parent_event=$(echo "$audit_tail" | jq --arg id "$PARENT_ID" '
  .events | map(select(.event_type == "agent_created" and .instance_id == $id)) | first
')
if [[ "$parent_event" == "null" || -z "$parent_event" ]]; then
  die "agent_created event for $PARENT_ID NOT found in last 200 chain entries"
fi
parent_data=$(echo "$parent_event" | jq -r '.event_json' | jq '.')
# The fields R2 must preserve exactly:
for field in instance_id agent_name role dna_full sibling_index constitution_source \
             constitution_hash soul_path constitution_path tools tool_catalog_version genre; do
  v=$(echo "$parent_data" | jq -r ".$field // \"__MISSING__\"")
  if [[ "$v" == "__MISSING__" ]]; then
    die "agent_created event missing field: $field"
  fi
done
# Negative check: parent_instance MUST NOT be on a /birth event.
pi=$(echo "$parent_data" | jq -r '.parent_instance // "absent"')
if [[ "$pi" != "absent" ]]; then
  die "agent_created event has unexpected parent_instance=$pi (should be absent)"
fi
ok "all required fields present, parent_instance correctly absent"

# ---- Step 3: spawn a child ---------------------------------------------
bar "3. POST /spawn — child of parent"
child_payload=$(jq -n --arg name "$CHILD_NAME" --arg pid "$PARENT_ID" '{
  profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false,
  tools_add: [{name: "delegate", version: "1"}],
  parent_instance_id: $pid
}')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST "$DAEMON/spawn" \
  -H "Content-Type: application/json" $(auth_header) -d "$child_payload")
child_body="$(cat "$tmp")"; rm -f "$tmp"
if [[ "$http_code" != "201" && "$http_code" != "200" ]]; then
  die "/spawn failed (http=$http_code): ${child_body:0:400}"
fi
CHILD_ID=$(echo "$child_body" | jq -r '.instance_id')
CHILD_DNA=$(echo "$child_body" | jq -r '.dna_full')
ok "child spawned  instance=$CHILD_ID  dna=${CHILD_DNA:0:12}…"

# ---- Step 4: agent_spawned event in chain ------------------------------
bar "4. agent_spawned event in chain"
audit_tail=$(curl -sf "$DAEMON/audit/tail?n=200")
child_event=$(echo "$audit_tail" | jq --arg id "$CHILD_ID" '
  .events | map(select(.event_type == "agent_spawned" and .instance_id == $id)) | first
')
if [[ "$child_event" == "null" || -z "$child_event" ]]; then
  die "agent_spawned event for $CHILD_ID NOT found in last 200 chain entries"
fi
child_data=$(echo "$child_event" | jq -r '.event_json' | jq '.')
# Spawn-only required fields:
for field in instance_id agent_name role dna_full sibling_index constitution_source \
             constitution_hash soul_path constitution_path tools tool_catalog_version genre \
             parent_instance parent_dna lineage_depth; do
  v=$(echo "$child_data" | jq -r ".$field // \"__MISSING__\"")
  if [[ "$v" == "__MISSING__" ]]; then
    die "agent_spawned event missing field: $field"
  fi
done
# Strong assertions on the parent-link:
got_pi=$(echo "$child_data" | jq -r '.parent_instance')
got_pdna=$(echo "$child_data" | jq -r '.parent_dna')
got_depth=$(echo "$child_data" | jq -r '.lineage_depth')
[[ "$got_pi" == "$PARENT_ID" ]] || die "parent_instance mismatch: got=$got_pi want=$PARENT_ID"
[[ "$got_pdna" == "$PARENT_DNA_SHORT" ]] || die "parent_dna mismatch: got=$got_pdna want=$PARENT_DNA_SHORT (short form, 12 chars)"
[[ "$got_depth" == "1" ]] || die "lineage_depth wrong: got=$got_depth want=1"
ok "parent_instance, parent_dna (short), lineage_depth=1 all correct"

# ---- Step 5: GET /agents/{id} for both ---------------------------------
bar "5. GET /agents/{id} for parent + child"
for id in "$PARENT_ID" "$CHILD_ID"; do
  hc=$(curl -s -o /dev/null -w "%{http_code}" "$DAEMON/agents/$id")
  [[ "$hc" == "200" ]] || die "GET /agents/$id returned $hc"
done
ok "both agents queryable"

# ---- Step 6: snapshot the event-data for diff vs post-refactor ---------
bar "6. snapshot event data → $BASELINE_OUT"
jq -n \
  --argjson parent "$parent_data" \
  --argjson child "$child_data" \
  '{parent_event_data: $parent, child_event_data: $child}' \
  > "$BASELINE_OUT"
ok "wrote $BASELINE_OUT (compare with diff after R2 lands)"

# ---- Step 7: cleanup — archive both ------------------------------------
bar "7. archive both test agents"
for id in "$CHILD_ID" "$PARENT_ID"; do
  arch_payload=$(jq -n --arg id "$id" '{instance_id: $id, reason: "R2 smoke cleanup"}')
  hc=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$DAEMON/archive" \
    -H "Content-Type: application/json" $(auth_header) -d "$arch_payload")
  [[ "$hc" == "200" ]] || printf "  warn: archive %s returned %s\n" "$id" "$hc"
done
ok "archived $PARENT_ID and $CHILD_ID"

bar "PASSED"
echo "Baseline saved to: $BASELINE_OUT"
echo "After R2 lands, re-run this script and diff the two files —"
echo "all fields except instance_id / dna / soul_path / constitution_path /"
echo "tool_catalog_version / sibling_index must match exactly."
echo ""
echo "Press return to close."
read -r _
