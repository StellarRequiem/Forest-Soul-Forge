#!/usr/bin/env bash
# Apply B142 Bug #1 fix runtime steps — restart the daemon to pick up
# the code change in tool_call.py, then reset the circuit breakers on
# the scheduled tasks I activated this session.
#
# The code edit (outcome.reason → outcome.exception_type) already
# landed via Claude's Edit tool; this script handles the runtime side.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# B149 (T25 follow-on): load FSF_API_TOKEN from .env if not in shell env
if [[ -f dev-tools/_fsf-env.sh ]]; then source dev-tools/_fsf-env.sh; fi

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. restart Forest daemon to pick up the code fix"
launchctl kickstart -k "gui/$(id -u)/dev.forest.daemon" \
  && echo "  ✓ kickstart -k sent" \
  || echo "  ✗ kickstart failed"

bar "2. wait up to 20s for /healthz"
for i in $(seq 1 20); do
  if curl -fsS --max-time 1 http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
    echo "  ✓ daemon back up after ${i}s"
    break
  fi
  printf '.'
  sleep 1
done
echo

bar "3. reset circuit breakers on my activated tasks"
TOKEN="${FSF_API_TOKEN:-}"
auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

for task_id in dashboard_watcher_healthz_5m signal_listener_audit_hourly status_reporter_daily_brief; do
  resp=$(curl -s -X POST "http://127.0.0.1:7423/scheduler/tasks/$task_id/reset" \
    -H "Content-Type: application/json" $(auth))
  if echo "$resp" | grep -q '"ok"'; then
    echo "  ✓ reset $task_id"
  else
    echo "  ? $task_id: ${resp:0:200}"
  fi
done

bar "4. verify scheduler state"
curl -fsS http://127.0.0.1:7423/scheduler/tasks 2>/dev/null \
  | jq -r '.tasks[] | select(.enabled==true) | "    \(.id | (. + "                                ")[:32])  enabled=\(.enabled)  breaker_open=\(.state.circuit_breaker_open)  last=\(.state.last_run_outcome // "never")  fails=\(.state.consecutive_failures)"' \
  2>/dev/null || echo "    (jq parse failed)"

bar "5. trigger dashboard_watcher_healthz_5m once to prove the fix"
echo "  Sending POST /scheduler/tasks/dashboard_watcher_healthz_5m/trigger..."
trigger_resp=$(curl -s -w "\n__HTTP__%{http_code}" -X POST \
  "http://127.0.0.1:7423/scheduler/tasks/dashboard_watcher_healthz_5m/trigger" $(auth))
echo "  HTTP: $(echo "$trigger_resp" | grep -oE '__HTTP__[0-9]+' | tr -d '_HTP')"
echo "  Body: $(echo "$trigger_resp" | sed 's/__HTTP__[0-9]*//' | head -c 300)"

echo ""
echo "Done. Press return to close."
read -r _
