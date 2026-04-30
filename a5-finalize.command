#!/usr/bin/env bash
# SW.A.5 — finalize: cleanup, push, restart daemon, run live-test.
#
# This script exists because the prior session's commit was made from a
# sandboxed bash that couldn't fully clean up .git/objects/tmp_obj_* and
# .git/HEAD.lock (Operation not permitted on cross-filesystem unlinks).
# The commit itself landed (HEAD = 5ef6747), but stale lock files would
# block future host-side git ops, and the new tools can't be exercised
# until the daemon reloads.
#
# Steps:
#   1. cleanup orphan .git temp files left behind by sandbox
#   2. push origin main
#   3. stop daemon (kill port 7423)
#   4. relaunch daemon in background, wait for /healthz
#   5. exec live-test-sw-coding-tools.command (which runs all 6 cases)
#
# Idempotent: safe to re-run if any step fails.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON_PORT=7423
DAEMON_LOG="$HERE/.run/daemon.log"

BLUE="\033[1;34m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
RESET="\033[0m"

say()  { printf "${BLUE}[a5]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[a5]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[a5]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[a5]${RESET} %s\n" "$*" 1>&2; }
hold() { echo ""; echo "Press return to close this window."; read -r _; }

# ---------- step 1: clean up sandbox-orphaned git state ------------------

say "Step 1/5 — cleaning up orphan .git state from sandbox commit..."
rm -f .git/HEAD.lock 2>/dev/null && ok "removed .git/HEAD.lock (was 0 bytes)" || ok "no .git/HEAD.lock to remove"
ORPHAN_COUNT="$(find .git/objects -name 'tmp_obj_*' -type f 2>/dev/null | wc -l | tr -d ' ')"
if [ "$ORPHAN_COUNT" -gt 0 ]; then
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
  ok "removed $ORPHAN_COUNT orphan tmp_obj_* files"
else
  ok "no orphan tmp_obj_* files"
fi

say "fsck:"
git fsck --no-dangling 2>&1 | sed 's/^/    /' || warn "fsck reported issues (see above)"

say "HEAD:"
git log -1 --oneline | sed 's/^/    /'

# ---------- step 2: push -------------------------------------------------

say "Step 2/5 — pushing origin main..."
if git push origin main 2>&1 | sed 's/^/    /'; then
  ok "push succeeded"
else
  err "push failed. Resolve manually (auth? remote diverged?) and re-run."
  hold
  exit 1
fi

# ---------- step 3: stop daemon ------------------------------------------

say "Step 3/5 — stopping daemon (port $DAEMON_PORT)..."
pids="$(lsof -nP -iTCP:$DAEMON_PORT -sTCP:LISTEN -t 2>/dev/null || true)"
if [ -n "$pids" ]; then
  warn "killing pids: $pids"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 0.5
  pids="$(lsof -nP -iTCP:$DAEMON_PORT -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    warn "force-killing: $pids"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.3
  fi
  ok "daemon stopped"
else
  ok "daemon was not running"
fi

# ---------- step 4: relaunch daemon in background ------------------------

say "Step 4/5 — relaunching daemon..."
if [ ! -x ".venv/bin/uvicorn" ]; then
  err ".venv/bin/uvicorn missing — run start.command first to bootstrap."
  hold
  exit 1
fi

mkdir -p .run
: > "$DAEMON_LOG"

# Fork uvicorn fully detached so this script can exit without killing it.
# (nohup + setsid + & + disown — belt, suspenders, pin.)
nohup .venv/bin/uvicorn forest_soul_forge.daemon.app:app \
  --host 127.0.0.1 --port "$DAEMON_PORT" \
  --log-level info \
  >> "$DAEMON_LOG" 2>&1 &
DAEMON_PID=$!
disown "$DAEMON_PID" 2>/dev/null || true

say "waiting for /healthz (pid $DAEMON_PID, timeout 40s)..."
for i in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${DAEMON_PORT}/healthz" >/dev/null 2>&1; then
    ok "daemon answered /healthz after ${i}s"
    break
  fi
  if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    err "daemon process exited before answering. Tail of log:"
    tail -40 "$DAEMON_LOG" 1>&2
    hold
    exit 1
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:${DAEMON_PORT}/healthz" >/dev/null 2>&1; then
  err "daemon never answered /healthz. Tail of log:"
  tail -40 "$DAEMON_LOG" 1>&2
  hold
  exit 1
fi

# Sanity: confirm the new tools actually loaded.
say "verifying new tools registered with daemon..."
TOOLS_JSON="$(curl -fsS "http://127.0.0.1:${DAEMON_PORT}/tools/registered" 2>/dev/null || echo '{}')"
for t in code_read code_edit shell_exec; do
  if echo "$TOOLS_JSON" | grep -q "\"$t\""; then
    ok "  ✓ $t.v1 registered"
  else
    err "  ✗ $t.v1 NOT in /tools/registered response — daemon didn't pick up the new builtin"
    err "  Tail of daemon log:"
    tail -20 "$DAEMON_LOG" 1>&2
    hold
    exit 1
  fi
done

# ---------- step 5: run live-test ----------------------------------------

say "Step 5/5 — running live-test-sw-coding-tools.command..."
echo ""
echo "==================================================================="
echo ""

if [ ! -x "live-test-sw-coding-tools.command" ]; then
  chmod +x live-test-sw-coding-tools.command 2>/dev/null || true
fi

if ./live-test-sw-coding-tools.command; then
  echo ""
  ok "============================================"
  ok "A.5 FINALIZED — all 5 steps green"
  ok "  • cleanup: done"
  ok "  • push: done"
  ok "  • daemon restart: done"
  ok "  • new tools registered: yes"
  ok "  • live-test: PASS"
  ok "============================================"
else
  echo ""
  err "============================================"
  err "live-test FAILED — see output above"
  err "Daemon is still running; commit + push already landed."
  err "Fix the failing case, then re-run live-test-sw-coding-tools.command directly."
  err "============================================"
  hold
  exit 1
fi

hold
