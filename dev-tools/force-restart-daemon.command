#!/usr/bin/env bash
# Aggressive port-7423 cleanup + daemon restart.
#
# stop.command kills lsof -sTCP:LISTEN matches; this also kills
# anything lingering on the port (CLOSE_WAIT, etc), unloads any
# launchd job that might be respawning, then re-fires start.command
# in this same window.

set -uo pipefail
cd "$(dirname "$0")/.."

DAEMON_PORT=7423
FRONT_PORT=5173

echo "=========================================================="
echo "force-restart-daemon"
echo "=========================================================="

# 1. Unload any launchd job that may be respawning the daemon.
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  echo "[1/5] Unloading launchd job ${PLIST_LABEL}..."
  launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
else
  echo "[1/5] No launchd job ${PLIST_LABEL} loaded — skip."
fi

# 2. Kill ANY process holding 7423 (LISTEN, CLOSE_WAIT, etc).
echo "[2/5] Killing any process on port ${DAEMON_PORT}..."
PIDS=$(lsof -nP -iTCP:${DAEMON_PORT} -t 2>/dev/null | sort -u)
if [ -n "$PIDS" ]; then
  echo "      Found PIDs: $PIDS"
  for pid in $PIDS; do
    kill -9 "$pid" 2>/dev/null && echo "      kill -9 $pid: ok" || echo "      kill -9 $pid: failed"
  done
else
  echo "      Nothing on port ${DAEMON_PORT}."
fi

# 3. Same for the frontend port.
echo "[3/5] Killing any process on port ${FRONT_PORT}..."
PIDS_F=$(lsof -nP -iTCP:${FRONT_PORT} -t 2>/dev/null | sort -u)
if [ -n "$PIDS_F" ]; then
  for pid in $PIDS_F; do
    kill -9 "$pid" 2>/dev/null && echo "      kill -9 $pid: ok"
  done
else
  echo "      Nothing on port ${FRONT_PORT}."
fi

# 4. Also kill any uvicorn / forest_soul_forge processes still around.
echo "[4/5] Killing stray uvicorn / forest_soul_forge processes..."
pkill -9 -f "uvicorn.*forest_soul_forge" 2>/dev/null && echo "      pkill: ok" || echo "      none."

# 5. Wait for ports to actually drain (TIME_WAIT can hold for ~30s).
echo "[5/5] Waiting for port ${DAEMON_PORT} to clear..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  if ! lsof -nP -iTCP:${DAEMON_PORT} >/dev/null 2>&1; then
    echo "      port ${DAEMON_PORT} clear after ${i}s."
    break
  fi
  sleep 1
done

echo
echo "=========================================================="
echo "Now starting fresh stack..."
echo "=========================================================="
echo

# Hand off to start.command. exec replaces this shell so log tailing
# happens in this window.
exec ./start.command
