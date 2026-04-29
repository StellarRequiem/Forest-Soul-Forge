#!/usr/bin/env bash
# Forest Soul Forge — soak test v1.
#
# Steady-state load against the live daemon. Designed to run unattended
# overnight (or for several hours). Not chaos — no failure injection.
# Just a low-volume continuous workload that exposes:
#   - memory leaks (RSS creep)
#   - WAL file growth (write-ahead log not checkpointing)
#   - audit chain corruption over thousands of writes
#   - registry bloat without cleanup
#   - any slow-burn race that needs hours of births to surface
#
# Cadence (slow option per agreed plan):
#   births:           every 60 sec
#   spawns:           every 300 sec (5 min)
#   tool dispatches:  every 180 sec (3 min)
#   RSS sample:       every 300 sec (5 min)
#   chain integrity:  every 900 sec (15 min)
#   cleanup:          every 1800 sec (30 min) — archive Soak_* agents > 1h old
#
# 24h projection: ~1440 births + ~288 spawns + ~480 dispatches + ~96
# integrity checks. Audit chain will grow ~3-4K events. Cleanup keeps
# active-agent count bounded.
#
# Logs: data/soak/soak-<unix-ts>.log (JSONL — one event per line).
# PID:  data/soak/soak.pid (kill via `kill $(cat data/soak/soak.pid)`).
#
# To launch unattended:
#   nohup ./soak.command > /dev/null 2>&1 &
#   (then close terminal — soak keeps running)
#
# To stop cleanly:
#   kill $(cat data/soak/soak.pid)
#   (or just kill -TERM <pid> — handler writes a final summary line)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
auth_header() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

mkdir -p data/soak
START_TS=$(date +%s)
LOG="data/soak/soak-$START_TS.log"
PIDFILE="data/soak/soak.pid"

# --- writes one JSONL line to the log ---------------------------------------
log() {
  # log <event_type> <key=value> [key=value ...]
  local et="$1"; shift
  local kv=""
  for arg in "$@"; do
    kv+=$(printf ',"%s":"%s"' "${arg%%=*}" "${arg#*=}")
  done
  printf '{"ts":"%s","elapsed_sec":%d,"event":"%s"%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(($(date +%s) - START_TS))" \
    "$et" "$kv" >> "$LOG"
}

# --- clean shutdown handler -------------------------------------------------
cleanup_and_exit() {
  log soak_stopped reason=signal pid=$$
  echo "" >&2
  echo "Soak stopped — log: $LOG" >&2
  rm -f "$PIDFILE"
  exit 0
}
trap cleanup_and_exit TERM INT

echo $$ > "$PIDFILE"
log soak_started pid=$$ daemon="$DAEMON" log_path="$LOG"
echo "Soak started (pid $$). Log: $LOG"
echo "Stop with: kill \$(cat $PIDFILE)"

# --- helpers ----------------------------------------------------------------
ROLES=("operator_companion" "anomaly_investigator" "log_analyst" "log_lurker")

# Returns 0 (success) on HTTP 2xx, 1 otherwise. Captures body to global $_BODY.
http_post() {
  local url="$1" payload="$2"
  local tmp; tmp="$(mktemp)"
  local code
  code=$(curl -s -o "$tmp" -w "%{http_code}" --max-time 30 -X POST "$url" \
    -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>/dev/null) || code=000
  _BODY="$(cat "$tmp")"; rm -f "$tmp"
  [[ "$code" =~ ^2 ]] && return 0 || { _BODY="$_BODY [http=$code]"; return 1; }
}

# --- birth one Soak_* agent -------------------------------------------------
do_birth() {
  local role="${ROLES[$RANDOM % ${#ROLES[@]}]}"
  local now; now=$(date +%s)
  local name="Soak_${role}_${now}"
  local payload
  payload=$(jq -n --arg name "$name" --arg role "$role" '{
    profile: {role: $role, trait_values: {}, domain_weight_overrides: {}},
    agent_name: $name,
    agent_version: "v1",
    enrich_narrative: false,
    tools_add: [{name: "memory_write", version: "1"}]
  }')
  if http_post "$DAEMON/birth" "$payload"; then
    local inst; inst=$(echo "$_BODY" | jq -r '.instance_id // "?"')
    log birth_ok role="$role" name="$name" instance="$inst"
  else
    log birth_failed role="$role" name="$name" detail="${_BODY:0:200}"
  fi
}

# --- spawn a child of the most-recent Soak_* agent --------------------------
do_spawn() {
  local parent
  parent=$(curl -sf --max-time 10 "$DAEMON/agents" \
    | jq -r '[.agents[] | select(.agent_name | startswith("Soak_")) | select(.status == "active")] | sort_by(.created_at) | last | .instance_id // ""' 2>/dev/null) || parent=""
  if [[ -z "$parent" ]]; then
    log spawn_skipped reason=no_parent
    return
  fi
  local now; now=$(date +%s)
  local name="SoakChild_${now}"
  local payload
  payload=$(jq -n --arg name "$name" --arg pid "$parent" '{
    profile: {role: "operator_companion", trait_values: {}, domain_weight_overrides: {}},
    agent_name: $name,
    agent_version: "v1",
    enrich_narrative: false,
    tools_add: [{name: "memory_write", version: "1"}],
    parent_instance_id: $pid
  }')
  if http_post "$DAEMON/spawn" "$payload"; then
    local inst; inst=$(echo "$_BODY" | jq -r '.instance_id // "?"')
    log spawn_ok parent="$parent" instance="$inst"
  else
    log spawn_failed parent="$parent" detail="${_BODY:0:200}"
  fi
}

# --- dispatch a memory_write on a random live Soak_* agent ------------------
do_dispatch() {
  local target
  target=$(curl -sf --max-time 10 "$DAEMON/agents" \
    | jq -r '[.agents[] | select(.agent_name | startswith("Soak_")) | select(.status == "active")] | if length == 0 then "" else .[(now * length / 1) % length | floor].instance_id end' 2>/dev/null) || target=""
  if [[ -z "$target" || "$target" == "null" ]]; then
    log dispatch_skipped reason=no_target
    return
  fi
  local payload
  # Endpoint: POST /agents/{instance_id}/tools/call
  # Body must include session_id (operator-supplied; per-session counter keys on it)
  payload=$(jq -n --arg session "soak-$(date +%s)" '{
    tool_name: "memory_write",
    tool_version: "1",
    session_id: $session,
    args: {layer: "episodic", scope: "private", tags: ["soak"], content: "soak heartbeat"}
  }')
  if http_post "$DAEMON/agents/$target/tools/call" "$payload"; then
    local status; status=$(echo "$_BODY" | jq -r '.status // "?"')
    log dispatch_ok target="$target" tool=memory_write result="$status"
  else
    log dispatch_failed target="$target" detail="${_BODY:0:200}"
  fi
}

# --- sample daemon RSS (resident set size in KB) + WAL size + chain length --
do_health_sample() {
  local pid rss wal chain_lines
  # Find the daemon python process listening on 7423
  pid=$(lsof -nP -iTCP:7423 -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $2; exit}') || pid=""
  if [[ -n "$pid" ]]; then
    rss=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ' || echo "?")
  else
    rss="?"; pid="?"
  fi
  wal=$(stat -f%z registry.sqlite-wal 2>/dev/null || echo "?")
  chain_lines=$(wc -l < examples/audit_chain.jsonl 2>/dev/null | tr -d ' ' || echo "?")
  log health_sample daemon_pid="$pid" rss_kb="$rss" wal_bytes="$wal" chain_lines="$chain_lines"
}

# --- integrity check on the audit chain (uses A.5 recipe) -------------------
do_integrity_check() {
  local result
  result=$(python3 - <<'PYEOF' 2>&1
import json, hashlib
GENESIS_PREV = "GENESIS"
PATH = "examples/audit_chain.jsonl"
errors = 0
prev = GENESIS_PREV
expected_seq = 0
n = 0
with open(PATH) as f:
    for line in f:
        if not line.strip(): continue
        e = json.loads(line)
        n += 1
        if e["seq"] != expected_seq: errors += 1
        expected_seq = e["seq"] + 1
        if e["prev_hash"] != prev: errors += 1
        prev = e["entry_hash"]
        body = {
            "seq":        e["seq"],
            "prev_hash":  e["prev_hash"],
            "agent_dna":  e.get("agent_dna"),
            "event_type": e["event_type"],
            "event_data": e["event_data"],
        }
        h = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if h != e["entry_hash"]: errors += 1
print(f"OK {n} {errors}" if errors == 0 else f"FAIL {n} {errors}")
PYEOF
  )
  if [[ "$result" == OK* ]]; then
    log integrity_ok chain_lines="${result##* }" entries="$(echo "$result" | awk '{print $2}')"
  else
    log integrity_failed detail="$result"
  fi
}

# --- cleanup: archive Soak_* agents older than 1 hour -----------------------
do_cleanup() {
  local cutoff_iso
  cutoff_iso=$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)
  local stale_ids
  stale_ids=$(curl -sf --max-time 10 "$DAEMON/agents" \
    | jq -r --arg cutoff "$cutoff_iso" '[.agents[] | select(.agent_name | startswith("Soak")) | select(.status == "active") | select(.created_at < $cutoff) | .instance_id] | .[]' 2>/dev/null || true)
  local count=0
  while IFS= read -r inst; do
    [[ -z "$inst" ]] && continue
    local payload; payload=$(jq -n --arg id "$inst" '{instance_id: $id, reason: "soak cleanup (>1h old)"}')
    if http_post "$DAEMON/archive" "$payload"; then
      count=$((count + 1))
    fi
  done <<< "$stale_ids"
  log cleanup_done archived="$count"
}

# ============================================================================
# MAIN LOOP — 30s tick. Each tick checks elapsed-since-start to decide which
# actions are due. Modulo math against the cadence intervals; the 30s tick
# means the worst-case lag for any cadence is 30s (negligible vs minutes).
# ============================================================================
TICK_SEC=30
BIRTH_INT=60
SPAWN_INT=300
DISPATCH_INT=180
HEALTH_INT=300
INTEGRITY_INT=900
CLEANUP_INT=1800

# Verify daemon reachable at startup
if ! curl -sf --max-time 5 "$DAEMON/healthz" > /dev/null; then
  log daemon_unreachable url="$DAEMON"
  echo "ERROR: daemon not reachable at $DAEMON" >&2
  rm -f "$PIDFILE"
  exit 1
fi
log daemon_ok url="$DAEMON"

# Initial health sample so the first row is captured immediately
do_health_sample

while true; do
  elapsed=$(($(date +%s) - START_TS))
  # Each cadence: fire when elapsed has crossed a fresh multiple of the interval.
  # Using `(( elapsed % INT < TICK_SEC ))` ensures exactly one fire per interval.
  if (( elapsed > 0 && elapsed % BIRTH_INT < TICK_SEC )); then
    do_birth
  fi
  if (( elapsed > 0 && elapsed % SPAWN_INT < TICK_SEC )); then
    do_spawn
  fi
  if (( elapsed > 0 && elapsed % DISPATCH_INT < TICK_SEC )); then
    do_dispatch
  fi
  if (( elapsed > 0 && elapsed % HEALTH_INT < TICK_SEC )); then
    do_health_sample
  fi
  if (( elapsed > 0 && elapsed % INTEGRITY_INT < TICK_SEC )); then
    do_integrity_check
  fi
  if (( elapsed > 0 && elapsed % CLEANUP_INT < TICK_SEC )); then
    do_cleanup
  fi
  sleep "$TICK_SEC"
done
