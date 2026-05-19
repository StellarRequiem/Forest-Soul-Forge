#!/usr/bin/env bash
# verify-burst86-scheduler.command — restart daemon, curl new endpoints.
#
# Burst 86 added the scheduler heartbeat in the daemon's asyncio
# lifespan. To pick up the change the daemon has to restart. This
# script does that + verifies the new HTTP surface responds correctly.
#
# Expected output:
#   * daemon starts and becomes healthy
#   * /scheduler/status returns running=true, task_count=0,
#     registered_runners=[]
#   * /scheduler/tasks returns count=0, tasks=[]
#   * If anything fails, the error surfaces in this terminal

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON_PORT=7423
DAEMON="http://127.0.0.1:${DAEMON_PORT}"
TOKEN="${FSF_API_TOKEN:-}"
auth_header() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

bar() { printf "\n========== %s ==========\n" "$1"; }
ok()  { printf "  ✓ %s\n" "$1"; }
no()  { printf "  ✗ %s\n" "$1"; }

bar "0. stop existing daemon (if running)"
pid=$(lsof -ti tcp:${DAEMON_PORT} 2>/dev/null || true)
if [[ -n "$pid" ]]; then
  echo "  daemon running on port ${DAEMON_PORT} (pid $pid) — sending SIGTERM"
  kill "$pid" 2>/dev/null || true
  for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.5
    if ! lsof -ti tcp:${DAEMON_PORT} >/dev/null 2>&1; then
      ok "daemon stopped"
      break
    fi
  done
  if lsof -ti tcp:${DAEMON_PORT} >/dev/null 2>&1; then
    no "daemon still up — sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
    sleep 1
  fi
else
  ok "no daemon running on ${DAEMON_PORT}"
fi

bar "1. start fresh daemon (background)"
mkdir -p .run
LOG=".run/daemon-burst86-test.log"
: > "$LOG"
# Start uvicorn in the background. Mirror what run.command does
# but without the frontend (we only care about the API for this test).
nohup .venv/bin/uvicorn \
    forest_soul_forge.daemon.app:app \
    --host 127.0.0.1 \
    --port "${DAEMON_PORT}" \
    --log-level info \
    >>"$LOG" 2>&1 &
DAEMON_PID=$!
echo "  daemon pid=$DAEMON_PID, log=$LOG"

bar "2. wait for /healthz"
for i in $(seq 1 60); do
  if curl -sf --max-time 2 "${DAEMON}/healthz" >/dev/null 2>&1; then
    ok "daemon healthy after ${i}s"
    break
  fi
  sleep 1
  if [[ "$i" == "60" ]]; then
    no "daemon never responded to /healthz in 60s. Tail of log:"
    tail -40 "$LOG" | sed 's/^/    /'
    echo "Press return to close."
    read -r _
    exit 1
  fi
done

bar "3. GET /scheduler/status"
status_resp=$(curl -s --max-time 5 "${DAEMON}/scheduler/status" $(auth_header))
echo "  raw response: $status_resp"
if echo "$status_resp" | jq . >/dev/null 2>&1; then
  ok "valid JSON"
  echo "$status_resp" | jq .
else
  no "not valid JSON"
fi

bar "4. GET /scheduler/tasks"
tasks_resp=$(curl -s --max-time 5 "${DAEMON}/scheduler/tasks" $(auth_header))
if echo "$tasks_resp" | jq . >/dev/null 2>&1; then
  ok "valid JSON"
  echo "$tasks_resp" | jq .
else
  no "not valid JSON"
  echo "  raw: $tasks_resp"
fi

bar "5. startup_diagnostics — was scheduler component recorded?"
diag_resp=$(curl -s --max-time 5 "${DAEMON}/healthz" $(auth_header))
echo "$diag_resp" | jq '.startup_diagnostics[]? | select(.component == "scheduler")' 2>/dev/null
if echo "$diag_resp" | jq -e '.startup_diagnostics[]? | select(.component == "scheduler")' >/dev/null 2>&1; then
  ok "scheduler component present in startup_diagnostics"
else
  no "scheduler component NOT in startup_diagnostics — is the lifespan integration wired?"
fi

bar "6. last 20 lines of daemon log"
tail -20 "$LOG" | sed 's/^/    /'

echo ""
echo "Verification complete. Daemon left running at $DAEMON (pid $DAEMON_PID)."
echo "To stop it: ./stop.command"
echo ""
echo "Press return to close."
read -r _
