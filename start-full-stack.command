#!/usr/bin/env bash
# start-full-stack.command — stop standalone daemon, bring up full stack.
#
# Used after verify-burst86-scheduler.command leaves a daemon running
# on port 7423 with no frontend. This stops it cleanly and hands off
# to run.command which starts both daemon + frontend + opens browser.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON_PORT=7423
FRONT_PORT=5173

# Stop existing daemon (the standalone one from verify-burst86)
pid=$(lsof -ti tcp:${DAEMON_PORT} 2>/dev/null || true)
if [[ -n "$pid" ]]; then
  echo "Stopping standalone daemon on ${DAEMON_PORT} (pid $pid)..."
  kill "$pid" 2>/dev/null || true
  for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.5
    if ! lsof -ti tcp:${DAEMON_PORT} >/dev/null 2>&1; then
      echo "  daemon stopped"
      break
    fi
  done
fi

# Stop frontend if hanging around
front_pid=$(lsof -ti tcp:${FRONT_PORT} 2>/dev/null || true)
if [[ -n "$front_pid" ]]; then
  kill "$front_pid" 2>/dev/null || true
  sleep 1
fi

# Hand off to run.command which brings up daemon + frontend + opens
# the browser to http://127.0.0.1:5173.
exec ./run.command
