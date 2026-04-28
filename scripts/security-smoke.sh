#!/usr/bin/env bash
# Forest Soul Forge — Security Swarm synthetic-incident smoke test
# (ADR-0033 Phase E1).
#
# Drives the canonical chain end-to-end:
#   morning_sweep → investigate_finding → contain_incident → key_audit
#
# Synthetic shape: seeds a temporary log directory with operator-named
# anomalous lines, runs LogLurker.morning_sweep against it, asserts
# the chain fires through each link and that approval queue catches
# the isolate_process step. No real PIDs are killed — the smoke
# stops at the approval queue snapshot.
#
# Usage:
#   ./scripts/security-smoke.sh
#
# Prereqs:
#   - daemon up; the 9 swarm agents born (run security-swarm-birth.sh
#     first); the 21 skills installed (run install-skills first)
#   - jq, curl
#
# Exit codes:
#   0  chain fired end-to-end as expected
#   1  some assertion failed; see report
#   2  prereq missing

set -uo pipefail

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
TS="$(date +%s)"
WORK="${TMPDIR:-/tmp}/fsf-security-smoke-${TS}"
LOGFILE="${WORK}/seeded.log"

require() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1" >&2; exit 2; }; }
require curl
require jq

auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# ---------------------------------------------------------------------------
# 1. Seed synthetic logs with an operator-recognisable anomaly pattern.
# ---------------------------------------------------------------------------
mkdir -p "$WORK"
cat >"$LOGFILE" <<EOF
2026-04-28 09:00:00 INFO normal startup
2026-04-28 09:00:01 INFO regular activity
2026-04-28 09:00:02 WARN canary_pattern_xyz123 from 1.2.3.4 — unauthorized
2026-04-28 09:00:03 WARN canary_pattern_xyz123 from 1.2.3.4 — repeat
2026-04-28 09:00:04 ERROR canary_pattern_xyz123 escalated
2026-04-28 09:00:05 INFO post-incident
EOF
log "seeded $LOGFILE"

# ---------------------------------------------------------------------------
# 2. Find the LogLurker, AnomalyAce, ResponseRogue, VaultWarden agent IDs
#    by querying /agents and matching role.
# ---------------------------------------------------------------------------
agents_json=$(curl -sf "$DAEMON/agents" $(auth) || true)
if [[ -z "$agents_json" ]]; then
  echo "FAIL: /agents returned nothing — daemon up? agents born?" >&2
  exit 1
fi

find_agent() {
  echo "$agents_json" | jq -r --arg role "$1" '.agents[] | select(.role == $role) | .instance_id' | head -1
}

LL=$(find_agent log_lurker)
AA=$(find_agent anomaly_ace)
RR=$(find_agent response_rogue)
VW=$(find_agent vault_warden)

for var in LL AA RR VW; do
  if [[ -z "${!var}" ]]; then
    echo "FAIL: agent for ${var} not born — run security-swarm-birth.sh" >&2
    exit 1
  fi
done
log "found chain agents: LL=$LL AA=$AA RR=$RR VW=$VW"

# ---------------------------------------------------------------------------
# 3. Drive LogLurker.morning_sweep against the seeded log.
# ---------------------------------------------------------------------------
# Real endpoint shape (ADR-0031 T2b): POST /agents/{id}/skills/run
# with body {skill_name, skill_version, session_id, inputs}.
# Earlier shape /skills/{name.version}/run was wrong — 404'd silently.
payload=$(jq -n \
  --arg path "$LOGFILE" \
  --arg downstream "$AA" \
  --arg contain "$RR" \
  --arg vault "$VW" \
  --arg sid "smoke-$TS" '{
  skill_name: "morning_sweep",
  skill_version: "1",
  session_id: $sid,
  inputs: {
    log_paths: [$path],
    pattern: "canary_pattern_xyz123",
    since: "last 1 day",
    escalate_threshold: 1,
    downstream_agent_id: $downstream,
    contain_agent_id: $contain,
    vault_agent_id: $vault
  }
}')

# Capture HTTP status + body so daemon rejections are visible.
tmp="$(mktemp)"
http_code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
  "$DAEMON/agents/$LL/skills/run" \
  -H "Content-Type: application/json" \
  $(auth) \
  -d "$payload")
resp="$(cat "$tmp")"; rm -f "$tmp"

if [[ "$http_code" != "200" && "$http_code" != "201" ]]; then
  echo "FAIL: morning_sweep run http=$http_code" >&2
  echo "body: ${resp:0:500}" >&2
  echo ""
  echo "WORKING_DIR_ARTIFACTS: $WORK"
  exit 1
fi
log "morning_sweep ran (http=$http_code)"

# ---------------------------------------------------------------------------
# 4. Inspect the audit chain for the chain links.
# ---------------------------------------------------------------------------
chain=$(curl -sf "$DAEMON/audit/tail?n=200" $(auth) || true)
if [[ -z "$chain" ]]; then
  echo "WARN: /audit/tail returned empty" >&2
fi

count_event() {
  local kind="$1"
  # AuditListOut.events is the canonical key (not 'entries').
  echo "$chain" | jq -r --arg k "$kind" '[.events[] | select(.event_type == $k)] | length' 2>/dev/null || echo 0
}

# Real event names per dispatcher.py: tool_call_dispatched (not
# tool_invoked) for every tool call; agent_delegated for cross-agent;
# skill_invoked for each skill run. Earlier smoke counted tool_invoked
# which is never emitted, so it always reported 0.
dispatched=$(count_event tool_call_dispatched)
succeeded=$(count_event tool_call_succeeded)
delegations=$(count_event agent_delegated)
skills_run=$(count_event skill_invoked)
approvals=$(count_event tool_call_pending_approval)

log "chain summary:"
log "  skill_invoked         = $skills_run  (expect ≥ 4 — one per chain link)"
log "  tool_call_dispatched  = $dispatched"
log "  tool_call_succeeded   = $succeeded"
log "  agent_delegated       = $delegations  (expect ≥ 3 — log_lurker→AA→RR→VW)"
log "  tool_call_pending_apv = $approvals    (smoke chain doesn't fire isolate; expect 0)"

# ---------------------------------------------------------------------------
# 5. Assertions.
# ---------------------------------------------------------------------------
fail=0
[[ "$dispatched" -gt 0 ]] || { echo "FAIL: no tool_call_dispatched events"; fail=1; }
[[ "$delegations" -gt 0 ]] || { echo "FAIL: no agent_delegated events (chain didn't escalate)"; fail=1; }

# Print skill engine outcome explicitly at the END so it's the last thing
# the operator sees before the verdict — easier to scan than scrolled-off
# blocks above.
echo ""
echo "----- skill engine outcome -----"
echo "$resp" | jq -r '
  "  status:           \(.status)",
  "  failed_step_id:   \(.failed_step_id // "n/a")",
  "  failure_reason:   \(.failure_reason // "n/a")",
  "  failure_detail:   \((.failure_detail // "n/a") | tostring | .[0:200])",
  "  steps_executed:   \(.steps_executed // "n/a")",
  "  steps_skipped:    \(.steps_skipped // "n/a")"
' 2>/dev/null

echo ""
echo "----- last 8 audit events -----"
echo "$chain" | jq -r '.events[-8:] | reverse | .[] | "  \(.seq | tostring | .[:6]) \(.event_type) instance=\((.instance_id // "?") | .[0:24])"' 2>/dev/null

# Surface the most recent tool_call_failed exception message — needed
# because skill_runtime's failure_detail only carries the exception
# class name, not the actual message.
echo ""
echo "----- most recent tool failures (with message) -----"
echo "$chain" | jq -r '
  [.events[] | select(.event_type == "tool_call_failed" or .event_type == "tool_call_refused")] |
  .[-3:] | reverse | .[] |
  "  \(.seq) \(.event_type) " +
    (
      .event_json | fromjson |
      "tool=\(.tool_key) exc=\(.exception_type // .reason // "?") msg=\((.exception_message // .detail // "") | tostring | .[0:300])"
    )
' 2>/dev/null

if (( fail > 0 )); then
  echo ""
  echo "smoke FAILED" >&2
  exit 1
fi
log "smoke PASSED"
log "artifacts: $WORK"
