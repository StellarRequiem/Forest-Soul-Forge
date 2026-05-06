#!/usr/bin/env bash
# Bump Ollama + Forest daemon BSD nice priority.
#
# CORRECTION: an earlier version of this script used `taskpolicy -c
# user-interactive -p PID`. That command is INVALID on macOS — the
# `-c` clamp only accepts utility/background/maintenance (it can only
# LOWER priority, not raise), and the `-p PID` form doesn't accept
# `-c` at all. The user-interactive QoS class is something processes
# opt INTO via libdispatch, not something an operator can impose from
# outside. macOS doesn't expose a "promote to user-interactive" knob.
#
# The correct way to prioritize an existing process on macOS is BSD
# nice via `renice`. Negative values = higher priority, positive =
# lower. Range: -20 to +20. Default: 0. Negative values require sudo.
#
# We use -10: noticeably higher than default but not aggressive
# enough to starve other system work. The kernel still gets first
# shot, but Ollama and Forest get scheduled ahead of generic
# background work (Spotlight reindex, Time Machine, etc.) when there's
# CPU contention.
#
# Inference itself runs on the GPU via Metal, so this won't speed
# up token generation — it keeps API responses snappy when the
# system is doing heavy background work.
#
# Idempotent — re-run anytime. Safe — pure scheduling hint, no state
# changes. Asks for sudo password (negative renice requires it).
#
# Pairs with the .env additions (OLLAMA_KEEP_ALIVE=-1,
# OLLAMA_NUM_PARALLEL=1) — those need an Ollama restart to take effect;
# this script's effects are immediate.

set -uo pipefail

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. find target processes"
ollama_pid=$(pgrep -f "ollama serve" 2>/dev/null | head -1 || echo "")
forest_pid=$(pgrep -f "forest_soul_forge.daemon" 2>/dev/null | head -1 || echo "")

if [[ -z "$ollama_pid" ]]; then
  echo "  ! Ollama not running (no 'ollama serve' process found)"
else
  current_nice=$(ps -o nice= -p "$ollama_pid" 2>/dev/null | tr -d ' ' || echo "?")
  echo "  ✓ Ollama PID: $ollama_pid (current nice: $current_nice)"
fi
if [[ -z "$forest_pid" ]]; then
  echo "  ! Forest daemon not running"
else
  current_nice=$(ps -o nice= -p "$forest_pid" 2>/dev/null | tr -d ' ' || echo "?")
  echo "  ✓ Forest daemon PID: $forest_pid (current nice: $current_nice)"
fi

if [[ -z "$ollama_pid" && -z "$forest_pid" ]]; then
  echo ""
  echo "Nothing to do. Bring up the stack first (./run.command or"
  echo "./start.command), then re-run this script."
  echo ""
  echo "Press return to close."
  read -r _
  exit 0
fi

bar "2. renice to -10 (sudo password required)"
echo "  About to run:"
[[ -n "$ollama_pid" ]] && echo "    sudo renice -n -10 -p $ollama_pid"
[[ -n "$forest_pid" ]] && echo "    sudo renice -n -10 -p $forest_pid"
echo ""

if [[ -n "$ollama_pid" ]]; then
  if sudo renice -n -10 -p "$ollama_pid" >/dev/null 2>&1; then
    new_nice=$(ps -o nice= -p "$ollama_pid" 2>/dev/null | tr -d ' ' || echo "?")
    echo "  ✓ Ollama renice succeeded (new nice: $new_nice)"
  else
    echo "  ✗ Ollama renice failed (rc=$?)"
  fi
fi
if [[ -n "$forest_pid" ]]; then
  if sudo renice -n -10 -p "$forest_pid" >/dev/null 2>&1; then
    new_nice=$(ps -o nice= -p "$forest_pid" 2>/dev/null | tr -d ' ' || echo "?")
    echo "  ✓ Forest daemon renice succeeded (new nice: $new_nice)"
  else
    echo "  ✗ Forest daemon renice failed (rc=$?)"
  fi
fi

bar "3. verify (ps shows current nice value)"
echo "  Snapshot:"
[[ -n "$ollama_pid" ]] && ps -o pid,nice,comm,user -p "$ollama_pid" 2>/dev/null | sed 's/^/    /'
[[ -n "$forest_pid" ]] && ps -o pid,nice,comm,user -p "$forest_pid" 2>/dev/null | sed 's/^/    /'

bar "4. reminder: KEEP_ALIVE needs Ollama restart"
cat <<'EOF'
  The .env now has OLLAMA_KEEP_ALIVE=-1 and OLLAMA_NUM_PARALLEL=1,
  but Ollama only reads its env on `ollama serve` startup. To pin
  the model in memory permanently:

    1. Quit Ollama.app from the menu bar (whale → Quit Ollama)
       OR: pkill -f "ollama serve"
    2. Reopen Ollama.app
       OR: cd /Users/llm01/Forest-Soul-Forge && ollama serve &
    3. Touch any model — `curl -fsS http://127.0.0.1:11434/api/generate \
       -d '{"model":"qwen2.5-coder:7b","prompt":"hi","stream":false}'`

  After that, the model stays loaded forever (until manually unloaded).
  Re-run this script after restart to re-renice priority (renice does
  NOT survive process restart — Ollama spawns at default nice 0 again).
EOF

echo ""
echo "Done. Press return to close."
read -r _
