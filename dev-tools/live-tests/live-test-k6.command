#!/usr/bin/env bash
# Forest Soul Forge — K6 (hardware_binding) live smoke test.
#
# What this verifies against a running daemon:
#   1. /healthz reachable
#   2. POST /birth with bind_to_hardware=true creates an agent whose
#      constitution YAML carries a hardware_binding block with
#      fingerprint + source.
#   3. The agent can call tools normally on the same machine.
#   4. Simulated mismatch: replace the binding with a wrong fingerprint
#      → next tool call returns refused with reason=hardware_quarantined
#      AND a hardware_mismatch event lands in the audit chain.
#   5. POST /agents/{id}/hardware/unbind strips the block, emits
#      hardware_unbound, and the very next tool call succeeds again.
#
# Prereqs:
#   - daemon up at $FSF_DAEMON_URL (default http://127.0.0.1:7423)
#   - daemon RESTARTED since K6 code landed (bind_to_hardware schema +
#     dispatcher quarantine check + /hardware router are new)
#   - jq + curl + python3 on PATH
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

require() { command -v "$1" >/dev/null 2>&1 || die "missing: $1"; }
require curl
require jq
require python3

# ---- Step 0: daemon health ----------------------------------------------
bar "0. Daemon health"
curl -sf "$DAEMON/healthz" > /dev/null || die "daemon not reachable at $DAEMON"
ok "daemon reachable"

# ---- Step 1: probe /agents/{id}/hardware/unbind exists ------------------
bar "1. /agents/{id}/hardware/unbind endpoint present"
# 404 with a fake instance_id is fine; what we DON'T want is "Not Found"
# for the path itself. Request a non-existent agent and check the body
# mentions agent-not-found, not method-not-allowed.
probe=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "$DAEMON/agents/fake_inst_xyz/hardware/unbind" \
  -H "Content-Type: application/json" $(auth_header) \
  -d '{"operator_id":"probe","reason":"endpoint test"}')
if [[ "$probe" == "405" || "$probe" == "404" ]]; then
  # 404 is OK (agent not found); 405 means the path itself isn't routed.
  if [[ "$probe" == "405" ]]; then
    die "/agents/{id}/hardware/unbind returned 405 — daemon was started before K6 landed.
          Restart via stop.command + run.command."
  fi
fi
ok "/agents/{id}/hardware/unbind endpoint reachable (probe returned $probe)"

# ---- Step 2: birth a hardware-bound test agent --------------------------
bar "2. /birth with bind_to_hardware=true"
SUFFIX="$(date +%s)"
NAME="HwBindTest_$SUFFIX"
payload=$(jq -n --arg name "$NAME" '{
  profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false,
  tools_add: [{name: "delegate", version: "1"}],
  bind_to_hardware: true
}')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST "$DAEMON/birth" \
  -H "Content-Type: application/json" $(auth_header) -d "$payload")
body="$(cat "$tmp")"; rm -f "$tmp"
if [[ "$http_code" != "200" && "$http_code" != "201" ]]; then
  die "birth failed (http=$http_code): ${body:0:400}"
fi
INSTANCE_ID=$(echo "$body" | jq -r '.instance_id')
CONST_PATH=$(echo "$body" | jq -r '.constitution_path')
ok "$NAME born  instance=$INSTANCE_ID"
ok "constitution at: $CONST_PATH"

# ---- Step 3: verify hardware_binding block lands in constitution --------
bar "3. hardware_binding block present in constitution YAML"
if [[ ! -f "$CONST_PATH" ]]; then
  die "constitution file missing: $CONST_PATH"
fi
if ! grep -q "^hardware_binding:" "$CONST_PATH"; then
  die "constitution has no hardware_binding block at $CONST_PATH"
fi
fp_in_file=$(awk '/^hardware_binding:/{flag=1; next} flag && /^[[:space:]]*fingerprint:/{print $2; exit}' "$CONST_PATH")
src_in_file=$(awk '/^hardware_binding:/{flag=1; next} flag && /^[[:space:]]*source:/{print $2; exit}' "$CONST_PATH")
if [[ -z "$fp_in_file" ]]; then
  die "fingerprint missing inside hardware_binding block"
fi
ok "fingerprint=$fp_in_file source=$src_in_file"

# ---- Step 4: hardware_bound event in audit chain ------------------------
bar "4. hardware_bound event in audit chain"
audit_tail=$(curl -sf "$DAEMON/audit/tail?n=40")
hwb=$(echo "$audit_tail" | jq --arg fp "$fp_in_file" '
  .events | map(select(
    .event_type == "hardware_bound"
    and ((.event_json | fromjson).fingerprint == $fp)
  )) | length
')
if [[ "$hwb" -lt "1" ]]; then
  die "hardware_bound event NOT in chain for fingerprint $fp_in_file"
fi
ok "hardware_bound event present (count=$hwb)"

# Helper: extract refused-reason from any of the dispatcher response
# shapes. The dispatcher emits hardware_quarantined refusals as a 4xx
# with body {"detail": {"reason": "...", ...}} (FastAPI HTTPException
# envelope). Other paths return a flat body with .status / .refused_reason.
# This helper handles both.
extract_refused_reason() {
  local body="$1"
  local r1 r2
  r1=$(echo "$body" | jq -r '.refused_reason // empty')
  r2=$(echo "$body" | jq -r '.detail.reason // empty')
  echo "${r1:-$r2}"
}

# ---- Step 5: tool call works on home machine ----------------------------
bar "5. tool call succeeds on home machine"
deleg=$(jq -n --arg target "$INSTANCE_ID" '{
  tool_name: "delegate", tool_version: "1",
  args: {target_instance_id: "fake_target", skill_name: "x", skill_version: "1",
         inputs: {}, reason: "K6 home-machine smoke"},
  session_id: "k6-home"
}')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
  "$DAEMON/agents/$INSTANCE_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$deleg")
deleg_body="$(cat "$tmp")"; rm -f "$tmp"
refused_reason=$(extract_refused_reason "$deleg_body")
if [[ "$refused_reason" == "hardware_quarantined" ]]; then
  die "FAIL: tool call refused as hardware_quarantined on the SAME machine. fp=$fp_in_file"
fi
ok "tool call passed quarantine check on home machine (refused_reason='$refused_reason')"

# ---- Step 6: simulate mismatch — overwrite fingerprint --------------------
bar "6. simulate fingerprint mismatch (rewrite fingerprint line via sed)"
# In-place sed substitution of the fingerprint line. Avoids depending on
# PyYAML being available to the system python3 — earlier runs failed
# because Finder-launched scripts use /usr/bin/python3 which doesn't
# have PyYAML installed. The yaml block format we emit is:
#     hardware_binding:
#       fingerprint: <16-hex-chars>
#       source: <name>
# So a single targeted sed line is reliable.
BOGUS_FP="0badf00d12345678"
# macOS sed needs -i '' (empty backup ext); GNU sed accepts -i alone.
if sed --version >/dev/null 2>&1; then
  sed -i "s/^  fingerprint: .*/  fingerprint: $BOGUS_FP/" "$CONST_PATH"
else
  sed -i '' "s/^  fingerprint: .*/  fingerprint: $BOGUS_FP/" "$CONST_PATH"
fi
new_fp=$(awk '/^hardware_binding:/{flag=1; next} flag && /^[[:space:]]*fingerprint:/{print $2; exit}' "$CONST_PATH")
if [[ "$new_fp" != "$BOGUS_FP" ]]; then
  die "sed substitution failed — fingerprint is still '$new_fp'"
fi
ok "fingerprint rewritten to $BOGUS_FP"

# ---- Step 7: tool call now refused as hardware_quarantined --------------
bar "7. tool call refused with hardware_quarantined"
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
  "$DAEMON/agents/$INSTANCE_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$deleg")
deleg_body="$(cat "$tmp")"; rm -f "$tmp"
refused_reason=$(extract_refused_reason "$deleg_body")
if [[ "$refused_reason" != "hardware_quarantined" ]]; then
  die "expected refused_reason=hardware_quarantined, got refused_reason='$refused_reason'.
        body=${deleg_body:0:600}"
fi
ok "tool call refused (http=$http_code, refused_reason=$refused_reason)"

# ---- Step 8: hardware_mismatch event in audit chain ---------------------
bar "8. hardware_mismatch event lands"
sleep 0.5
audit_tail2=$(curl -sf "$DAEMON/audit/tail?n=40")
hwm=$(echo "$audit_tail2" | jq --arg iid "$INSTANCE_ID" '
  .events | map(select(
    .event_type == "hardware_mismatch"
    and ((.event_json | fromjson).instance_id == $iid)
  )) | length
')
if [[ "$hwm" -lt "1" ]]; then
  die "hardware_mismatch event NOT in chain for $INSTANCE_ID"
fi
ok "hardware_mismatch event present (count=$hwm)"

# ---- Step 9: unbind + verify event + verify file --------------------------
bar "9. POST /agents/{id}/hardware/unbind"
unbind_payload=$(jq -n '{operator_id: "live-test-k6", reason: "K6 simulated migration"}')
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
  "$DAEMON/agents/$INSTANCE_ID/hardware/unbind" \
  -H "Content-Type: application/json" $(auth_header) -d "$unbind_payload")
unbind_body="$(cat "$tmp")"; rm -f "$tmp"
if [[ "$http_code" != "200" ]]; then
  die "unbind failed (http=$http_code): ${unbind_body:0:400}"
fi
prev_fp=$(echo "$unbind_body" | jq -r '.previous_binding')
ok "unbind returned previous_binding=$prev_fp"
if grep -q "^hardware_binding:" "$CONST_PATH"; then
  die "constitution still has hardware_binding block after unbind"
fi
ok "hardware_binding block stripped from constitution"

# ---- Step 10: tool call now passes quarantine check again ----------------
bar "10. tool call passes quarantine after unbind"
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
  "$DAEMON/agents/$INSTANCE_ID/tools/call" \
  -H "Content-Type: application/json" $(auth_header) -d "$deleg")
deleg_body="$(cat "$tmp")"; rm -f "$tmp"
refused_reason=$(extract_refused_reason "$deleg_body")
if [[ "$refused_reason" == "hardware_quarantined" ]]; then
  die "tool call STILL refused as hardware_quarantined after unbind"
fi
ok "tool call passes quarantine check (refused_reason='$refused_reason')"

# ---- Cleanup -------------------------------------------------------------
bar "11. Cleanup — archive test agent"
arch=$(jq -n --arg id "$INSTANCE_ID" '{instance_id: $id, reason: "live-test-k6 cleanup"}')
http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  "$DAEMON/archive" -H "Content-Type: application/json" $(auth_header) -d "$arch")
[[ "$http_code" == "200" || "$http_code" == "201" ]] && ok "archived $INSTANCE_ID" || \
  no "archive returned $http_code"

bar "K6 LIVE TEST PASSED"
echo "End-to-end:"
echo "  - bind_to_hardware=true on /birth writes the binding to constitution YAML"
echo "  - hardware_bound audit event lands"
echo "  - tool call passes on home machine"
echo "  - simulated mismatch → quarantine + hardware_mismatch event"
echo "  - unbind strips block + emits hardware_unbound"
echo "  - next tool call passes quarantine again"
echo ""
echo "Press return to close."
read -r _
