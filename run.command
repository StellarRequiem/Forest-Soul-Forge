#!/usr/bin/env bash
# Forest Soul Forge — local dev launcher.
#
# Starts the FastAPI daemon on 127.0.0.1:7423 and a static HTTP server
# for the frontend on 127.0.0.1:5173, then opens the browser.
#
# Why 5173 for the frontend? The daemon's default CORS allowlist already
# includes http://127.0.0.1:5173, so there's nothing to configure — the
# browser's preflight just passes.
#
# Ctrl-C in the Terminal window cleans both processes up.

set -u

# cd to the script's own directory so this works no matter where it was
# double-clicked from.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON_PORT=7423
FRONT_PORT=5173

BLUE="\033[1;34m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
DIM="\033[2m"
RESET="\033[0m"

say()  { printf "${BLUE}[fsf]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[fsf]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[fsf]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[fsf]${RESET} %s\n" "$*" 1>&2; }

# ---------- preflight ----------------------------------------------------

if [ ! -x ".venv/bin/python" ]; then
  err "No .venv/bin/python found at $HERE/.venv."
  err "Create it first:  python3 -m venv .venv && .venv/bin/pip install -e ."
  echo ""
  echo "Press return to close this window."
  read -r _
  exit 1
fi

if ! .venv/bin/python -c "import forest_soul_forge.daemon.app" >/dev/null 2>&1; then
  err "forest_soul_forge package not importable from .venv."
  err "Run:  .venv/bin/pip install -e ."
  echo ""
  echo "Press return to close this window."
  read -r _
  exit 1
fi

if [ ! -d "frontend" ]; then
  err "No frontend/ directory at $HERE."
  exit 1
fi

# ---------- port cleanup -------------------------------------------------
# If a previous launcher is still holding the ports, take them back. We
# only kill processes listening on those exact ports — nothing else.

kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    warn "Port $port busy (pids: $pids). Killing."
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 0.5
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

kill_port "$DAEMON_PORT"
kill_port "$FRONT_PORT"

# ---------- start daemon -------------------------------------------------

mkdir -p .run
DAEMON_LOG="$HERE/.run/daemon.log"
FRONT_LOG="$HERE/.run/frontend.log"
: > "$DAEMON_LOG"
: > "$FRONT_LOG"

say "Starting daemon on 127.0.0.1:${DAEMON_PORT} ..."
.venv/bin/uvicorn forest_soul_forge.daemon.app:app \
  --host 127.0.0.1 --port "$DAEMON_PORT" \
  --log-level info \
  >> "$DAEMON_LOG" 2>&1 &
DAEMON_PID=$!

say "Starting frontend on 127.0.0.1:${FRONT_PORT} ..."
(cd frontend && ../.venv/bin/python -m http.server "$FRONT_PORT" --bind 127.0.0.1) \
  >> "$FRONT_LOG" 2>&1 &
FRONT_PID=$!

# ---------- wait for daemon to answer ------------------------------------

say "Waiting for /healthz ..."
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${DAEMON_PORT}/healthz" >/dev/null 2>&1; then
    ok "Daemon is up."
    break
  fi
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    err "Daemon process exited before answering /healthz. Tail of log:"
    tail -40 "$DAEMON_LOG" 1>&2
    kill "$FRONT_PID" 2>/dev/null || true
    echo ""
    echo "Press return to close this window."
    read -r _
    exit 1
  fi
  sleep 0.25
done

# ---------- open browser -------------------------------------------------

URL="http://127.0.0.1:${FRONT_PORT}/?api=http://127.0.0.1:${DAEMON_PORT}"
ok "Opening ${URL}"
open "$URL" || warn "Couldn't auto-open the browser. Paste the URL above."

# ---------- foreground: tail logs, trap Ctrl-C ---------------------------

cleanup() {
  echo ""
  say "Shutting down ..."
  kill "$DAEMON_PID" 2>/dev/null || true
  kill "$FRONT_PID"  2>/dev/null || true
  sleep 0.3
  kill -9 "$DAEMON_PID" 2>/dev/null || true
  kill -9 "$FRONT_PID"  2>/dev/null || true
  ok "Clean."
  exit 0
}
trap cleanup INT TERM

echo ""
printf "${DIM}── logs (daemon + frontend, Ctrl-C to stop) ───────────────────${RESET}\n"
# Tail both logs interleaved.
tail -n 0 -F "$DAEMON_LOG" "$FRONT_LOG"
