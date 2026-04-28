#!/usr/bin/env bash
# Forest Soul Forge — stop the local stack.
#
# Kills any process listening on the daemon (7423) and frontend (5173)
# ports. Safe to double-click; only those two ports are touched.
#
# Use this when:
#   - you started the stack via start.command/run.command and closed
#     the Terminal window without Ctrl-C, leaving the processes running
#   - the stack is misbehaving and you want a clean restart
#   - port 7423 or 5173 is blocked by a leftover process
#
# After running this, double-click start.command to bring the stack
# back up.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON_PORT=7423
FRONT_PORT=5173

BLUE="\033[1;34m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RESET="\033[0m"

say()  { printf "${BLUE}[stop]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[stop]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[stop]${RESET} %s\n" "$*"; }

kill_port() {
  local port="$1"
  local label="$2"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    ok "${label} port ${port}: nothing listening."
    return
  fi
  warn "${label} port ${port}: killing pids ${pids}"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 0.5
  # Re-check; force-kill anything still hanging.
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    warn "${label} port ${port}: force-killing pids ${pids}"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.2
  fi
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    warn "${label} port ${port}: still has listeners (pids ${pids}). Manual intervention needed."
  else
    ok "${label} port ${port}: cleared."
  fi
}

say "Stopping Forest Soul Forge stack..."
kill_port "$DAEMON_PORT" "Daemon"
kill_port "$FRONT_PORT"  "Frontend"
ok "Done."

# Brief pause so the user can read the output before the window closes.
echo ""
echo "Press return to close this window."
read -r _
