#!/usr/bin/env bash
# T22 verification — confirm B143's per-thread connection fix also
# resolves the scheduled-task dispatch failures that surfaced after
# B142.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# B149 (T25 follow-on): load FSF_API_TOKEN from .env if not in shell env
if [[ -f dev-tools/_fsf-env.sh ]]; then source dev-tools/_fsf-env.sh; fi

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. reset all 3 active breakers (any may have tripped between B142 and B143)"
TOKEN="${FSF_API_TOKEN:-}"
auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

for task_id in dashboard_watcher_healthz_5m signal_listener_audit_hourly status_reporter_daily_brief; do
  resp=$(curl -s -X POST "http://127.0.0.1:7423/scheduler/tasks/$task_id/reset" \
    -H "Content-Type: application/json" $(auth))
  if echo "$resp" | grep -q '"ok"'; then
    echo "  ✓ reset $task_id"
  else
    echo "  ? $task_id: ${resp:0:160}"
  fi
done

bar "2. trigger dashboard_watcher_healthz_5m (was failing post-B142)"
resp=$(curl -s -X POST \
  "http://127.0.0.1:7423/scheduler/tasks/dashboard_watcher_healthz_5m/trigger" $(auth))
echo "  Response:"
echo "$resp" | python3 -m json.tool 2>/dev/null | sed 's/^/    /' || echo "    ${resp:0:300}"
outcome=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('outcome','?'))" 2>/dev/null)
if [[ "$outcome" == "succeeded" ]]; then
  echo "  ✅ B143 ALSO FIXED the scheduler dispatch (was 'failed' post-B142)"
elif [[ "$outcome" == "failed" ]]; then
  echo "  ⚠️  Still failing — different bug. Check err log."
else
  echo "  ? unexpected outcome: $outcome"
fi

bar "3. trigger signal_listener_audit_hourly for second-source confirmation"
resp=$(curl -s -X POST \
  "http://127.0.0.1:7423/scheduler/tasks/signal_listener_audit_hourly/trigger" $(auth))
outcome=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('outcome','?'))" 2>/dev/null)
echo "  signal_listener outcome: $outcome"

bar "4. final scheduler state"
curl -fsS http://127.0.0.1:7423/scheduler/tasks 2>/dev/null \
  | jq -r '.tasks[] | select(.enabled==true) | "    \(.id | (. + "                                ")[:32])  enabled=\(.enabled)  breaker=\(.state.circuit_breaker_open)  last=\(.state.last_run_outcome // "never")  succ=\(.state.total_successes)  fail=\(.state.total_failures)"' \
  2>/dev/null

bar "5. tail err log for any new failures"
tail -30 /tmp/forest-daemon.err.log > _diagnostic_t22_err.txt 2>&1
echo "  wrote _diagnostic_t22_err.txt ($(wc -l < _diagnostic_t22_err.txt | tr -d ' ') lines)"

echo ""
echo "Done. Press return to close."
read -r _
