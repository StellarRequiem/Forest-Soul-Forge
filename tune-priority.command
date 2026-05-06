#!/usr/bin/env bash
# Bump Ollama + Forest daemon to user-interactive QoS.
#
# macOS uses Apple's Quality-of-Service (QoS) classes plus BSD nice
# values to schedule work. By default both Ollama and Forest land in
# the "default" QoS bucket, where they compete with Spotlight reindex,
# Time Machine, photo analysis, etc. for CPU.
#
# `taskpolicy -c user-interactive` promotes them to the highest
# non-realtime tier — same priority as a foreground app the user is
# actively interacting with. Inference itself runs on the GPU via Metal,
# so this won't speed up token generation, but it keeps API responses
# snappy when the system is doing other heavy background work.
#
# Re-run anytime — idempotent. Safe — no state changes, just scheduling
# hints. Asks for sudo password (taskpolicy modifies process attrs).
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
  echo "  ✓ Ollama PID: $ollama_pid"
fi
if [[ -z "$forest_pid" ]]; then
  echo "  ! Forest daemon not running"
else
  echo "  ✓ Forest daemon PID: $forest_pid"
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

bar "2. promote to user-interactive QoS (sudo password required)"
echo "  About to run:"
[[ -n "$ollama_pid" ]] && echo "    sudo taskpolicy -c user-interactive -p $ollama_pid"
[[ -n "$forest_pid" ]] && echo "    sudo taskpolicy -c user-interactive -p $forest_pid"
echo ""

if [[ -n "$ollama_pid" ]]; then
  if sudo taskpolicy -c user-interactive -p "$ollama_pid"; then
    echo "  ✓ Ollama promoted to user-interactive"
  else
    echo "  ✗ failed to promote Ollama (rc=$?)"
  fi
fi
if [[ -n "$forest_pid" ]]; then
  if sudo taskpolicy -c user-interactive -p "$forest_pid"; then
    echo "  ✓ Forest daemon promoted to user-interactive"
  else
    echo "  ✗ failed to promote Forest daemon (rc=$?)"
  fi
fi

bar "3. verify (taskpolicy -G shows current policy)"
[[ -n "$ollama_pid" ]] && {
  echo "  Ollama:"
  sudo taskpolicy -G -p "$ollama_pid" 2>&1 | sed 's/^/    /'
}
[[ -n "$forest_pid" ]] && {
  echo "  Forest daemon:"
  sudo taskpolicy -G -p "$forest_pid" 2>&1 | sed 's/^/    /'
}

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
  Re-run this script after restart to re-promote priority.
EOF

echo ""
echo "Done. Press return to close."
read -r _
