#!/usr/bin/env bash
# One-shot: restart daemon + run diagnostics
set -uo pipefail
cd "$(dirname "$0")"

echo "=== STEP 1: Kill existing daemon ==="
PIDS=$(lsof -nP -iTCP:7423 -t 2>/dev/null | sort -u)
if [ -n "$PIDS" ]; then
  echo "Killing PIDs: $PIDS"
  for pid in $PIDS; do
    kill -9 "$pid" 2>/dev/null && echo "  killed $pid" || echo "  failed $pid"
  done
  sleep 2
else
  echo "No process on port 7423."
fi

# Also kill stray uvicorn
pkill -9 -f "uvicorn.*forest_soul_forge" 2>/dev/null || true
sleep 1

echo ""
echo "=== STEP 2: Start daemon ==="
if [ ! -x ".venv/bin/python" ]; then
  echo "ERROR: .venv/bin/python not found"
  exit 1
fi

mkdir -p .run
DAEMON_LOG="$(pwd)/.run/daemon.log"
: > "$DAEMON_LOG"

.venv/bin/uvicorn forest_soul_forge.daemon.app:app \
  --host 127.0.0.1 --port 7423 \
  --log-level info \
  >> "$DAEMON_LOG" 2>&1 &
DAEMON_PID=$!
echo "Daemon PID: $DAEMON_PID"

echo ""
echo "=== STEP 3: Wait for health ==="
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:7423/healthz" >/dev/null 2>&1; then
    echo "Daemon healthy after ${i}s"
    break
  fi
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    echo "ERROR: Daemon died. Log tail:"
    tail -40 "$DAEMON_LOG"
    exit 1
  fi
  sleep 1
done

# Verify health endpoint
HEALTH=$(curl -sf "http://127.0.0.1:7423/healthz" 2>&1)
echo "Health response: $HEALTH"
echo ""

echo "=== STEP 4: Run diagnostic harness ==="
bash dev-tools/diagnostic/diagnostic-all.command 2>&1 | tail -80

echo ""
echo "=== STEP 5: Find latest summary ==="
LATEST=$(ls -t data/test-runs/ 2>/dev/null | grep '^diagnostic-all-' | head -1)
if [ -n "$LATEST" ]; then
  echo "Latest run: $LATEST"
  echo ""
  echo "=== SUMMARY CONTENTS ==="
  cat "data/test-runs/${LATEST}/summary.md" 2>/dev/null || echo "No summary.md found"
else
  echo "No diagnostic-all runs found in data/test-runs/"
fi

echo ""
echo "=== DONE ==="
echo "Press return to close."
read -r _
