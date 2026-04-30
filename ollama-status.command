#!/usr/bin/env bash
# Ollama state survey — one-shot diagnostic.
#
# Tells us:
#   - Is native Ollama running on the Mac (port 11434)?
#   - Is Docker Ollama running (fsf-ollama container)?
#   - Which models are pulled?
#   - What's the host RAM (so we can size models with headroom)?
#   - What does the FSF daemon think the provider state is?
#
# No mutations. Pure read.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. host hardware"
echo "  Mac model:    $(sysctl -n hw.model 2>/dev/null || echo '?')"
echo "  CPU brand:    $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo '?')"
total_mem_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo "0")
total_mem_gb=$((total_mem_bytes / 1024 / 1024 / 1024))
echo "  RAM total:    ${total_mem_gb} GiB"
echo "  Free pages:   $(vm_stat 2>/dev/null | grep 'free' | awk '{print $3}' | tr -d '.' || echo '?')"

bar "2. native Ollama on 127.0.0.1:11434?"
if curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags > /tmp/ollama_tags.json 2>/dev/null; then
  echo "  ✓ native Ollama responsive"
  echo ""
  echo "  Models pulled:"
  python3 -c "
import json, sys
data = json.load(open('/tmp/ollama_tags.json'))
models = data.get('models', [])
if not models:
    print('    (none — none pulled)')
else:
    for m in models:
        size_gb = m.get('size', 0) / (1024**3)
        family = (m.get('details', {}).get('family') or '?')
        params = (m.get('details', {}).get('parameter_size') or '?')
        print(f'    {m[\"name\"]:35s}  {size_gb:5.2f} GB  family={family}  params={params}')
"
else
  echo "  ✗ native Ollama NOT responding"
  if pgrep -x Ollama >/dev/null 2>&1; then
    echo "    (Ollama.app is running but port 11434 isn't responsive — check Ollama's settings)"
  elif command -v ollama >/dev/null 2>&1; then
    echo "    (CLI found at $(command -v ollama) — try running 'ollama serve' or open Ollama.app)"
  else
    echo "    (no native Ollama installed — install via 'brew install ollama' or download from ollama.com)"
  fi
fi

bar "3. Docker Ollama (fsf-ollama container)?"
if command -v docker >/dev/null 2>&1; then
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^fsf-ollama$'; then
    echo "  ✓ fsf-ollama container running"
    docker exec fsf-ollama ollama list 2>/dev/null | head -10
  else
    echo "  ✗ fsf-ollama container NOT running"
    echo "    (start with: docker compose --profile llm up -d)"
  fi
else
  echo "  ✗ docker CLI not found"
fi

bar "4. FSF daemon provider state"
if curl -fsS --max-time 3 http://127.0.0.1:7423/runtime/provider 2>/dev/null | python3 -m json.tool; then
  :
else
  echo "  ✗ daemon /runtime/provider not reachable"
fi

bar "5. recommended models for swarm work (qwen family for coding)"
echo "  Given ${total_mem_gb} GiB total RAM, with ~${total_mem_gb}-8 GiB headroom for OS+browser+IDE:"
if [[ $total_mem_gb -ge 32 ]]; then
  echo "    → qwen2.5-coder:14b  (~9 GB)  — best coding quality, fits easily"
  echo "    → qwen2.5:14b        (~9 GB)  — best general reasoning"
  echo "    → qwen2.5-coder:7b   (~4.7 GB) — recommended for the triune (your pick)"
elif [[ $total_mem_gb -ge 16 ]]; then
  echo "    → qwen2.5-coder:7b   (~4.7 GB) — recommended for the triune (your pick) ✓"
  echo "    → qwen2.5:7b         (~4.7 GB) — fallback if coder model isn't ideal"
  echo "    → phi3.5:3.8b        (~2.2 GB) — lighter alternative if RAM is tight"
elif [[ $total_mem_gb -ge 8 ]]; then
  echo "    → phi3.5:3.8b        (~2.2 GB) — best you can run comfortably"
  echo "    → qwen2.5-coder:3b   (~2.0 GB) — smaller coder model"
else
  echo "    → llama3.2:1b        (~1.3 GB) — small but workable"
fi

echo ""
echo "Press return to close."
read -r _
