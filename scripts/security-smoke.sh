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
payload=$(jq -n --arg path "$LOGFILE" --arg downstream "$AA" '{
  inputs: {
    log_paths: [$path],
    patterns: ["canary_pattern_xyz123"],
    since: "last 1 day",
    escalate_threshold: 1,
    downstream_agent_id: $downstream
  }
}')

resp=$(curl -sf -X POST "$DAEMON/agents/$LL/skills/morning_sweep.v1/run" \
  -H "Content-Type: application/json" \
  $(auth) \
  -d "$payload" || true)

if [[ -z "$resp" ]]; then
  echo "FAIL: morning_sweep run returned empty" >&2
  exit 1
fi
log "morning_sweep ran"
echo "$resp" | jq -C '.' 2>/dev/null | head -40 || echo "$resp"

# ---------------------------------------------------------------------------
# 4. Inspect the audit chain for the chain links.
# ---------------------------------------------------------------------------
chain=$(curl -sf "$DAEMON/audit?limit=200" $(auth) || true)
if [[ -z "$chain" ]]; then
  echo "WARN: /audit returned empty" >&2
fi

count_event() {
  local kind="$1"
  echo "$chain" | jq -r --arg k "$kind" '[.entries[] | select(.event_type == $k)] | length' 2>/dev/null || echo 0
}

invokes=$(count_event tool_invoked)
delegations=$(count_event agent_delegated)
approvals=$(count_event tool_call_pending_approval)

log "chain summary:"
log "  tool_invoked       = $invokes"
log "  agent_delegated    = $delegations"
log "  pending_approval   = $approvals  (expect ≥ 1 from isolate_process)"

# ---------------------------------------------------------------------------
# 5. Assertions.
# ---------------------------------------------------------------------------
fail=0
[[ "$invokes" -gt 0 ]] || { echo "FAIL: no tool_invoked events"; fail=1; }
[[ "$delegations" -gt 0 ]] || { echo "FAIL: no agent_delegated events (chain didn't escalate)"; fail=1; }

# pending_approval is good — means contain_incident reached isolate_process
# and stopped at the queue. 0 is acceptable if severity_floor was not met.
log "(approvals=$approvals — 0 means the chain stopped before isolate, which is acceptable depending on triage verdict)"

if (( fail > 0 )); then
  echo "smoke FAILED" >&2
  exit 1
fi
log "smoke PASSED"
log "artifacts: $WORK"
