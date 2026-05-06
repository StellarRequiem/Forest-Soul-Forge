#!/usr/bin/env bash
# Restart Ollama.app with KEEP_ALIVE pinning, end-to-end.
#
# Why this exists separately from the .env edit:
#
# Ollama.app launched from /Applications/Ollama.app reads its
# environment from launchd's per-user (gui/$UID) domain — NOT from
# Forest's .env file. Putting OLLAMA_KEEP_ALIVE=-1 in /Users/llm01/
# Forest-Soul-Forge/.env had no effect on Ollama.app's behavior; that
# file is read by Forest's daemon when it boots, and by tooling that
# explicitly sources it. Ollama doesn't.
#
# The correct macOS path is `launchctl setenv` in the user's gui
# domain. This script:
#
#   1. Sets OLLAMA_KEEP_ALIVE=-1 + OLLAMA_NUM_PARALLEL=1 in launchd
#   2. Quits Ollama.app via AppleScript (gentle quit; lets Ollama
#      finish in-flight requests + flush state)
#   3. Reopens Ollama.app — it inherits the launchd env on launch
#   4. Waits for the API to come back
#   5. Touches qwen2.5-coder:7b to trigger an initial load (this is
#      what activates KEEP_ALIVE — Ollama only honors it for models
#      that have been loaded at least once)
#
# After this runs the qwen2.5-coder:7b model stays in RAM forever
# (until you `ollama stop` it or restart Ollama again). 24/7
# specialist agents can hit dispatch with no warmup latency.
#
# launchctl setenv values persist until logout. To make them survive
# logout, add them to ~/.zprofile or ~/Library/LaunchAgents/ — but
# for now they live for the current login session. Re-run this after
# logout/reboot to re-pin.
#
# Idempotent. Re-runnable.

set -uo pipefail

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. set Ollama env in launchd (gui/$UID domain — no sudo needed)"
launchctl setenv OLLAMA_KEEP_ALIVE -1 \
  && echo "  ✓ OLLAMA_KEEP_ALIVE=-1 set" \
  || echo "  ✗ failed to set OLLAMA_KEEP_ALIVE"
launchctl setenv OLLAMA_NUM_PARALLEL 1 \
  && echo "  ✓ OLLAMA_NUM_PARALLEL=1 set" \
  || echo "  ✗ failed to set OLLAMA_NUM_PARALLEL"

bar "2. verify (launchctl getenv)"
echo "  OLLAMA_KEEP_ALIVE=$(launchctl getenv OLLAMA_KEEP_ALIVE)"
echo "  OLLAMA_NUM_PARALLEL=$(launchctl getenv OLLAMA_NUM_PARALLEL)"

bar "3. quit Ollama.app (gentle)"
if pgrep -ix Ollama >/dev/null 2>&1; then
  osascript -e 'tell application "Ollama" to quit' 2>/dev/null \
    && echo "  ✓ quit signal sent" \
    || echo "  ✗ quit failed"
  echo "  waiting up to 10s for clean shutdown..."
  for i in $(seq 1 10); do
    if ! pgrep -ix Ollama >/dev/null 2>&1; then
      echo "  ✓ Ollama exited after ${i}s"
      break
    fi
    sleep 1
  done
  if pgrep -ix Ollama >/dev/null 2>&1; then
    echo "  ! Ollama still running after 10s — force-killing"
    pkill -9 -ix Ollama 2>/dev/null || true
    sleep 2
  fi
else
  echo "  - Ollama wasn't running"
fi

bar "4. reopen Ollama.app"
open -a Ollama \
  && echo "  ✓ open command sent" \
  || echo "  ✗ open failed"

bar "5. wait for API to come back (up to 20s)"
for i in $(seq 1 20); do
  if curl -fsS --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "  ✓ API responsive after ${i}s"
    break
  fi
  printf '.'
  sleep 1
done
echo

bar "6. touch qwen2.5-coder:7b to activate KEEP_ALIVE on it"
echo "  Sending one-token generate request..."
if curl -fsS --max-time 60 http://127.0.0.1:11434/api/generate \
     -H 'Content-Type: application/json' \
     -d '{"model":"qwen2.5-coder:7b","prompt":"hi","stream":false,"options":{"num_predict":1}}' \
     >/dev/null 2>&1; then
  echo "  ✓ model touched and loaded"
else
  echo "  ✗ generate failed — check ollama logs"
fi

bar "7. confirm model is loaded + sticky"
echo "  ollama ps (currently loaded models):"
ollama ps 2>/dev/null | sed 's/^/    /' || echo "    (ollama ps failed)"

echo ""
echo "  /api/tags (all pulled models):"
curl -fsS http://127.0.0.1:11434/api/tags 2>/dev/null \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f'    {m[\"name\"]:30s}  {m[\"size\"]/(1024**3):.2f} GB') for m in d.get('models',[])]" \
  2>/dev/null || echo "    (failed to parse)"

echo ""
echo "Done. KEEP_ALIVE active for any newly-loaded model. The qwen2.5-"
echo "coder:7b model is loaded now and will stay loaded until ollama"
echo "is restarted or you 'ollama stop qwen2.5-coder:7b'."
echo ""
echo "Press return to close."
read -r _
