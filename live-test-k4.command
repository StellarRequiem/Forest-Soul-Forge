#!/usr/bin/env bash
# Forest Soul Forge — K4 (triune spawn template) live smoke test.
#
# What this verifies against a running daemon:
#
#   1. /triune/bond endpoint exists (daemon was restarted with K4 code)
#   2. /birth × 3 → bond → constitution files get triune block patched
#   3. delegate.v1 to a NON-bonded target raises "triune restriction"
#      AND emits an out_of_triune_attempt audit event
#   4. The bond ceremony event lands in the audit chain
#
# The 3 test agents are archived at the end so this is reusable.
#
# Prereqs:
#   - daemon up at $FSF_DAEMON_URL (default http://127.0.0.1:7423)
#   - daemon RESTARTED since the K4 code landed (added /triune router)
#   - jq + curl on PATH
#
# Double-click from Finder. Output streams in order; stops on first
# unrecoverable failure.
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

require() { command -v "$1" >/dev/null 2>&1 || die "missing: $1 — install via Homebrew"; }
require curl
require jq

# ---- Step 0: daemon health -----------------------------------------------
bar "0. Daemon health"
if ! curl -sf "$DAEMON/healthz" > /tmp/k4-health.$$ 2>&1; then
  die "daemon not reachable at $DAEMON — start it via run.command first"
fi
ok "daemon reachable at $DAEMON"
rm -f /tmp/k4-health.$$

# ---- Step 1: detect /triune/bond endpoint --------------------------------
bar "1. /triune/bond endpoint present"
# Hit the endpoint with an empty body — expect 422 (validation error) on a
# K4-loaded daemon, 404 on an old daemon.
endpoint_probe=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "$DAEMON/triune/bond" -H "Content-Type: application/json" -d '{}')
if [[ "$endpoint_probe" == "404" ]]; then
  die "/triune/bond returned 404 — daemon was started before K4 code landed.
        Restart the daemon: stop.command then start.command (or run.command)."
fi
if [[ "$endpoint_probe" != "422" ]]; then
  die "/triune/bond probe returned unexpected $endpoint_probe (expected 422 for empty body)"
fi
ok "/triune/bond endpoint present (returned 422 on empty body, as expected)"

# ---- Step 2: birth 3 test agents -----------------------------------------
bar "2. Birth 3 test agents (operator_companion role)"
SUFFIX="$(date +%s)"
NAMES=("HeartwoodTest_$SUFFIX" "BranchTest_$SUFFIX" "LeafTest_$SUFFIX")
INSTANCE_IDS=()
CONSTITUTION_PATHS=()

birth_one() {
  local name="$1"
  local payload http_code body tmp
  # tools_add: ["delegate.v1"] — operator_companion's archetype kit doesn't
  # include delegate.v1, but the triune enforcement test needs it. Adding
  # via tools_add is the documented per-birth override path.
  payload=$(jq -n --arg name "$name" '{
    profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
    agent_name: $name,
    agent_version: "v1",
    enrich_narrative: false,
    tools_add: [{name: "delegate", version: "1"}]
  }')
  tmp="$(mktemp)"
  http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST "$DAEMON/birth" \
    -H "Content-Type: application/json" $(auth_header) -d "$payload")
  body="$(cat "$tmp")"; rm -f "$tmp"
  if [[ "$http_code" != "200" && "$http_code" != "201" ]]; then
    die "birth failed for $name (http=$http_code): ${body:0:300}"
  fi
  echo "$body"
}

for name in "${NAMES[@]}"; do
  body=$(birth_one "$name")
  inst=$(echo "$body" | jq -r '.instance_id')
  cpath=$(echo "$body" | jq -r '.constitution_path')
  INSTANCE_IDS+=("$inst")
  CONSTITUTION_PATHS+=("$cpath")
  ok "$name  instance=$inst"
done

# ---- Step 3: bond them ---------------------------------------------------
bar "3. POST /triune/bond"
bond_payload=$(jq -n \
  --arg name "test_$SUFFIX" \
  --arg op "live-test-k4" \
  --arg id1 "${INSTANCE_IDS[0]}" \
  --arg id2 "${INSTANCE_IDS[1]}" \
  --arg id3 "${INSTANCE_IDS[2]}" \
  '{bond_name: $name, instance_ids: [$id1, $id2, $id3], operator_id: $op, restrict_delegations: true}')

tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST "$DAEMON/triune/bond" \
  -H "Content-Type: application/json" $(auth_header) -d "$bond_payload")
bond_body="$(cat "$tmp")"; rm -f "$tmp"
if [[ "$http_code" != "200" && "$http_code" != "201" ]]; then
  die "/triune/bond failed (http=$http_code): ${bond_body:0:400}"
fi
ok "bond endpoint returned $http_code"
echo "$bond_body" | jq '.'
ceremony_seq=$(echo "$bond_body" | jq -r '.ceremony_seq')

# ---- Step 4: verify constitution files patched ---------------------------
# Uses grep/sed instead of `python3 -c` so we don't depend on PyYAML being
# installed in the system Python (the daemon's venv has it, but a script run
# from Finder uses /usr/bin/python3, which doesn't).
bar "4. Verify each constitution YAML got the triune block"
expected_bond="test_$SUFFIX"
for i in 0 1 2; do
  path="${CONSTITUTION_PATHS[$i]}"
  if [[ ! -f "$path" ]]; then
    die "constitution file missing: $path"
  fi
  if ! grep -q "^triune:" "$path"; then
    die "constitution[$i] has no triune block at $path"
  fi
  # YAML block fields are indented under 'triune:'; pull the 3 lines that
  # follow it. We only need bond_name + restrict_delegations + the two
  # partner ids — exact YAML formatting doesn't matter, just presence.
  triune_block="$(awk '/^triune:/{flag=1; next} flag && /^[^ ]/{flag=0} flag' "$path")"
  bond_name="$(echo "$triune_block" | grep -E '^[[:space:]]*bond_name:' | sed -E 's/.*bond_name:[[:space:]]*//; s/^[\"'\'']//; s/[\"'\'']$//' | head -1)"
  restrict="$(echo "$triune_block" | grep -E '^[[:space:]]*restrict_delegations:' | sed -E 's/.*restrict_delegations:[[:space:]]*//' | head -1)"
  partner_count="$(echo "$triune_block" | grep -cE '^[[:space:]]*-[[:space:]]')"
  if [[ "$bond_name" != "$expected_bond" ]]; then
    die "constitution[$i] bond_name mismatch: got '$bond_name' expected '$expected_bond' (block was: $triune_block)"
  fi
  if [[ "$restrict" != "true" ]]; then
    die "constitution[$i] restrict_delegations not true: got '$restrict'"
  fi
  if [[ "$partner_count" != "2" ]]; then
    die "constitution[$i] should have 2 partners, got $partner_count (block was: $triune_block)"
  fi
  ok "constitution[$i] bond=$bond_name partners=$partner_count restrict=$restrict"
done

# ---- Step 5: verify ceremony event in audit chain ------------------------
bar "5. Verify triune.bonded ceremony event landed"
# Endpoint param is `n=` not `limit=`. Pull a generous tail so concurrent
# background activity doesn't push our event off the bottom.
audit_tail=$(curl -sf "$DAEMON/audit/tail?n=80")
ceremony_found=$(echo "$audit_tail" | jq --arg seq "$ceremony_seq" '
  .events | map(select(.event_type == "ceremony" and (.seq | tostring) == $seq)) | length
')
if [[ "$ceremony_found" != "1" ]]; then
  die "ceremony event seq=$ceremony_seq not found in /audit/tail. Got: $(echo "$audit_tail" | jq '.events | map(.event_type)')"
fi
ok "triune.bonded ceremony event present in chain (seq=$ceremony_seq)"

# ---- Step 6: delegate.v1 to a NON-bonded target → should refuse ---------
bar "6. Triune enforcement — delegate to non-sister should refuse"
# Birth a 4th agent that is NOT in the bond.
outsider_body=$(birth_one "OutsiderTest_$SUFFIX")
outsider_id=$(echo "$outsider_body" | jq -r '.instance_id')
ok "outsider born: $outsider_id"

# Try delegate from heartwood → outsider (NOT a sister). Expect 4xx with
# "triune restriction" in the body.
deleg_payload=$(jq -n --arg target "$outsider_id" '{
  tool_name: "delegate", tool_version: "1",
  args: {
    target_instance_id: $target,
    skill_name: "noop", skill_version: "1",
    inputs: {},
    reason: "live-test: should refuse — outsider is not in triune"
  },
  session_id: "live-test-k4"
}')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
  "$DAEMON/agents/${INSTANCE_IDS[0]}/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$deleg_payload")
deleg_body="$(cat "$tmp")"; rm -f "$tmp"

# Two-part verification because the dispatcher response doesn't include
# the exception message text — only the exception type. The ground truth
# is the audit chain.
#
# Part A: response must show status=failed (otherwise the call wasn't
# rejected at all, which would be a real K4 bug).
deleg_status=$(echo "$deleg_body" | jq -r '.status // ""')
deleg_exc=$(echo "$deleg_body" | jq -r '.failure_exception_type // ""')
if [[ "$deleg_status" != "failed" ]]; then
  die "delegate to outsider returned status='$deleg_status' (expected 'failed').
        http=$http_code body=${deleg_body:0:600}"
fi
if [[ "$deleg_exc" != "ToolValidationError" ]]; then
  die "delegate failure_exception_type='$deleg_exc' (expected 'ToolValidationError').
        body=${deleg_body:0:600}"
fi
ok "delegate to outsider rejected (status=failed, exception=ToolValidationError, http=$http_code)"

# Part B: audit chain must show out_of_triune_attempt for this triune.
# We match by bond_name (which is unique per test run via $SUFFIX) since
# event_data field may be serialized differently than expected.
bar "7. Verify out_of_triune_attempt event landed for THIS triune"
sleep 1
audit_tail2=$(curl -sf "$DAEMON/audit/tail?n=80")
expected_bond="test_$SUFFIX"
# event_json is a serialized string per AuditEventOut schema — parse it
# inside jq to access nested fields like bond_name.
matching=$(echo "$audit_tail2" | jq --arg bond "$expected_bond" '
  .events | map(select(
    .event_type == "out_of_triune_attempt"
    and ((.event_json | fromjson).bond_name == $bond)
  )) | length
')
if [[ "$matching" -lt "1" ]]; then
  die "out_of_triune_attempt event NOT found for bond='$expected_bond'.
        Recent event types: $(echo "$audit_tail2" | jq '.events | map(.event_type)')"
fi
ok "out_of_triune_attempt event present for THIS bond (count=$matching)"
ok "  → triune restriction enforced AND visible in audit chain — both safety properties verified"

# ---- Cleanup: archive all 4 test agents ---------------------------------
# Endpoint is POST /archive (writes router), with instance_id in the body —
# not /agents/{id}/archive. Schema: {instance_id, reason}.
bar "8. Cleanup — archive test agents"
for inst in "${INSTANCE_IDS[@]}" "$outsider_id"; do
  arch_payload=$(jq -n --arg id "$inst" --arg reason "live-test-k4 cleanup" \
    '{instance_id: $id, reason: $reason}')
  http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "$DAEMON/archive" \
    -H "Content-Type: application/json" $(auth_header) -d "$arch_payload")
  if [[ "$http_code" == "200" || "$http_code" == "201" ]]; then
    ok "archived $inst"
  else
    no "archive of $inst returned $http_code (test agents may need manual cleanup)"
  fi
done

bar "K4 LIVE TEST PASSED"
echo "Triune mechanics verified end-to-end against the live daemon:"
echo "  - /triune/bond endpoint accepts requests"
echo "  - constitution YAML files get patched with the triune block"
echo "  - ceremony event lands in the audit chain"
echo "  - delegate.v1 enforcement refuses out-of-bond targets"
echo "  - out_of_triune_attempt event lands when refused"
echo ""
echo "Press return to close."
read -r _
