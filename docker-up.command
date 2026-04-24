#!/usr/bin/env bash
# Forest Soul Forge — docker compose up launcher.
#
# Double-click from Finder. Builds the images if they don't exist,
# brings up daemon + frontend (no ollama by default — add --profile llm
# via the docker-up-llm.command sibling), waits for health, opens the
# browser, and tails logs in this window.
#
# Ctrl-C cleans up (docker compose down).

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

BLUE="\033[1;34m"; GREEN="\033[1;32m"; YELLOW="\033[1;33m"; RED="\033[1;31m"; DIM="\033[2m"; RESET="\033[0m"
say()  { printf "${BLUE}[fsf-docker]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[fsf-docker]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[fsf-docker]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[fsf-docker]${RESET} %s\n" "$*" 1>&2; }

# ---------- preflight ----------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
  err "docker CLI not found. Install Docker Desktop for Mac, then re-run."
  echo ""; echo "Press return to close."; read -r _; exit 1
fi

if ! docker info >/dev/null 2>&1; then
  err "Docker Desktop isn't running. Start it, wait for the whale icon to stop pulsing, then re-run."
  echo ""; echo "Press return to close."; read -r _; exit 1
fi

# Free host ports the compose stack wants. If the local-dev launcher
# (run.command) is still running we'd rather take them over than fail.
kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    warn "Port $port busy (pids: $pids). Freeing."
    kill $pids 2>/dev/null || true
    sleep 0.5
    kill -9 $pids 2>/dev/null || true
  fi
}
kill_port 7423
kill_port 5173

# ---------- build + up ---------------------------------------------------

say "Building images (first run is slow — base images pull, then pip install)..."
if ! docker compose build; then
  err "Build failed. Scroll up for the error."
  echo ""; echo "Press return to close."; read -r _; exit 1
fi
ok "Build complete."

say "Bringing up daemon + frontend..."
docker compose up -d
ok "Containers started. Streaming health ..."

# ---------- wait for daemon /healthz ------------------------------------

say "Waiting for daemon /healthz ..."
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
    ok "Daemon up."
    break
  fi
  # Fail fast if compose killed the daemon container.
  if ! docker compose ps --format json daemon 2>/dev/null | grep -q '"State":"running"'; then
    err "Daemon container exited before /healthz answered. docker compose logs daemon:"
    docker compose logs --tail=60 daemon 1>&2
    echo ""; echo "Press return to close."; read -r _; exit 1
  fi
  sleep 0.5
done

# ---------- open browser -------------------------------------------------

URL="http://127.0.0.1:5173/?api=http://127.0.0.1:7423"
ok "Opening ${URL}"
open "$URL" || warn "Couldn't auto-open the browser. Paste the URL above."

# ---------- foreground: tail logs, trap Ctrl-C --------------------------

cleanup() {
  echo ""
  say "Ctrl-C received — stopping compose stack ..."
  docker compose down
  ok "Down. Clean."
  exit 0
}
trap cleanup INT TERM

echo ""
printf "${DIM}── docker compose logs (Ctrl-C to stop everything) ───────────${RESET}\n"
docker compose logs -f --tail=20
