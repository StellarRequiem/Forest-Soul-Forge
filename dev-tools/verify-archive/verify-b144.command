#!/usr/bin/env bash
# B144 verification — restart daemon to load fix, reset breakers,
# trigger scheduled tasks, expect outcome=succeeded (was outcome=failed).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# B149 (T25 follow-on): load FSF_API_TOKEN from .env if not in shell env
if [[ -f dev-tools/_fsf-env.sh ]]; then source dev-tools/_fsf-env.sh; fi

bar() { printf '\n========== %s ==========\n' "$1"; }
TOKEN="${FSF_API_TOKEN:-}"
auth() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

bar "1. restart Forest daemon to load B144"
launchctl kickstart -k "gui/$(id -u)/dev.forest.daemon" \
  && echo "  ✓ kickstart -k sent" \
  || { echo "  ✗ kickstart failed"; echo "Press return to close."; read -r _; exit 1; }

bar "2. wait up to 20s for /healthz"
for i in $(seq 1 20); do
  if curl -fsS --max-time 1 http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
    echo "  ✓ daemon back up after ${i}s"
    break
  fi
  printf '.'; sleep 1
done
echo

bar "3. reset all 3 breakers"
for task_id in dashboard_watcher_healthz_5m signal_listener_audit_hourly status_reporter_daily_brief; do
  curl -s -X POST "http://127.0.0.1:7423/scheduler/tasks/$task_id/reset" \
    -H "Content-Type: application/json" $(auth) >/dev/null
  echo "  ✓ reset $task_id"
done

bar "4. trigger dashboard_watcher_healthz_5m (was failing pre-B144)"
resp=$(curl -s -X POST \
  "http://127.0.0.1:7423/scheduler/tasks/dashboard_watcher_healthz_5m/trigger" $(auth))
echo "$resp" | python3 -m json.tool 2>/dev/null | sed 's/^/    /' || echo "    ${resp:0:300}"
outcome=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('outcome','?'))" 2>/dev/null)
if [[ "$outcome" == "succeeded" ]]; then
  echo "  ✅ B144 FIX VERIFIED — dashboard_watcher dispatch succeeded (was 'failed')"
elif [[ "$outcome" == "failed" ]]; then
  echo "  ❌ Still failing — check audit chain for the exact exception"
else
  echo "  ? unexpected outcome: $outcome"
fi

bar "5. trigger signal_listener_audit_hourly (second-source confirmation)"
resp=$(curl -s -X POST \
  "http://127.0.0.1:7423/scheduler/tasks/signal_listener_audit_hourly/trigger" $(auth))
outcome=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('outcome','?'))" 2>/dev/null)
echo "  signal_listener outcome: $outcome"

bar "6. trigger status_reporter_daily_brief (third-source confirmation)"
resp=$(curl -s -X POST \
  "http://127.0.0.1:7423/scheduler/tasks/status_reporter_daily_brief/trigger" $(auth))
outcome=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('outcome','?'))" 2>/dev/null)
echo "  status_reporter outcome: $outcome"

bar "7. final scheduler state"
curl -fsS http://127.0.0.1:7423/scheduler/tasks 2>/dev/null \
  | jq -r '.tasks[] | select(.enabled==true) | "    \(.id | (. + "                                ")[:32])  enabled=\(.enabled)  breaker=\(.state.circuit_breaker_open)  last=\(.state.last_run_outcome // "never")  succ=\(.state.total_successes)  fail=\(.state.total_failures)"' \
  2>/dev/null

bar "8. dump latest audit chain entries (last 10) to confirm tool_call_succeeded fired"
tail -10 examples/audit_chain.jsonl > _diagnostic_b144_chain.txt 2>&1 || echo "    couldn't tail audit chain"
echo "  wrote _diagnostic_b144_chain.txt"
echo "  recent event types:"
python3 -c "
import json
with open('examples/audit_chain.jsonl') as f:
    lines = f.readlines()[-10:]
for line in lines:
    try:
        e = json.loads(line)
        print(f'    [{e[\"seq\"]}] {e[\"event_type\"]}')
    except: pass
"

echo ""
echo "Done. Press return to close."
read -r _
