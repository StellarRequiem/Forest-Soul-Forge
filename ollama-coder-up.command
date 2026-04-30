#!/usr/bin/env bash
# Native-Ollama launcher for the coding swarm.
#
# Brings up the host's native Ollama (Apple Silicon GPU via Metal —
# 5-10x faster than the Docker variant for inference), then pulls
# qwen2.5-coder:7b if it's not already present.
#
# Why native over Docker: Apple Silicon Ollama uses the unified GPU.
# The Docker variant runs in a Linux VM and falls back to CPU for
# Metal-only models, which means a 7B token-stream that should be
# 30-50 tok/s drops to 2-5 tok/s. For a coding swarm where the agents
# will be doing real reasoning, that difference matters.
#
# Idempotent: re-running just verifies state, doesn't re-pull.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

MODEL="qwen2.5-coder:7b"
OLLAMA_URL="http://127.0.0.1:11434"

bar() { printf '\n========== %s ==========\n' "$1"; }
ok()  { printf "  ✓ %s\n" "$1"; }
no()  { printf "  ✗ %s\n" "$1" >&2; }
die() { no "$1"; echo ""; echo "Press return to close."; read -r _; exit 1; }

# ---- Step 1: locate Ollama ------------------------------------------------
bar "1. locate native Ollama"
if [[ -x /opt/homebrew/bin/ollama ]]; then
  OLLAMA_BIN=/opt/homebrew/bin/ollama
  ok "found CLI at $OLLAMA_BIN"
elif command -v ollama >/dev/null 2>&1; then
  OLLAMA_BIN=$(command -v ollama)
  ok "found CLI at $OLLAMA_BIN"
else
  die "Ollama not installed. Install via 'brew install ollama' or download from ollama.com"
fi

# ---- Step 2: ensure server is running -------------------------------------
bar "2. ensure server responding on $OLLAMA_URL"
if curl -fsS --max-time 3 "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
  ok "server already responsive"
else
  echo "  starting Ollama in background..."
  # `ollama serve` is the idiomatic way to run the daemon. We start it
  # detached so this script can exit cleanly while the server keeps
  # running. Logs land in ~/.ollama/logs/ on macOS.
  nohup "$OLLAMA_BIN" serve > /tmp/ollama-serve.log 2>&1 &
  echo "  waiting for /api/tags (up to 30s)..."
  for i in $(seq 1 30); do
    if curl -fsS --max-time 1 "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
      ok "server responsive after ${i}s"
      break
    fi
    [[ $i -eq 30 ]] && die "server didn't come up within 30s — check /tmp/ollama-serve.log"
    sleep 1
  done
fi

# ---- Step 3: pull qwen2.5-coder:7b if not present ------------------------
bar "3. ensure $MODEL is pulled"
have_model=$(curl -fsS "$OLLAMA_URL/api/tags" | python3 -c "
import json, sys
data = json.load(sys.stdin)
models = data.get('models', [])
print('yes' if any(m['name'] == '$MODEL' for m in models) else 'no')
")

if [[ "$have_model" == "yes" ]]; then
  ok "$MODEL already pulled"
else
  echo "  pulling $MODEL (~4.7 GB — this will take a few minutes on first run)..."
  echo ""
  "$OLLAMA_BIN" pull "$MODEL"
  ok "$MODEL pulled successfully"
fi

# ---- Step 4: warm-load the model + smoke test ----------------------------
bar "4. warm-load + smoke test"
echo "  asking the model to identify itself..."
response=$(curl -sf --max-time 60 -X POST "$OLLAMA_URL/api/generate" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg m "$MODEL" '{model: $m, prompt: "Reply with exactly: SWARM READY", stream: false, options: {num_predict: 20}}')" \
  | jq -r '.response // ""')

if [[ -n "$response" ]]; then
  ok "model responding: ${response:0:80}"
else
  die "model didn't respond to smoke test"
fi

# ---- Step 5: list everything that's pulled -------------------------------
bar "5. local model inventory"
"$OLLAMA_BIN" list

bar "DONE"
echo ""
echo "Ollama is running with $MODEL loaded."
echo "The FSF daemon will pick this up on next start (env: FSF_LOCAL_MODEL=$MODEL in .env)."
echo ""
echo "Next: stop + restart the daemon (stop.command + run.command), then"
echo "      verify GET /runtime/provider shows local=ok with $MODEL loaded."
echo ""
echo "Press return to close."
read -r _
