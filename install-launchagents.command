#!/usr/bin/env bash
# Install Forest's launchd agents — Layer 3 of the 24/7 ops setup.
#
# Substitutes Ollama's binary path into the template, copies both
# plists to ~/Library/LaunchAgents/, stops any currently-running
# Ollama.app and Forest daemon (so they don't conflict on ports
# 11434 + 7423), then bootstraps both into launchd.
#
# After this runs, both daemons:
#   - Auto-start at login (RunAtLoad=true)
#   - Auto-restart on crash (KeepAlive=true)
#   - Run with EnvironmentVariables baked in (KEEP_ALIVE=-1, etc.)
#   - Log to /tmp/{ollama,forest-daemon}.{out,err}.log
#
# To revert: ./uninstall-launchagents.command
#
# Tradeoff: this replaces Ollama.app with a headless Ollama serve.
# No menu bar UI. If you want the .app back, uninstall + re-enable
# Ollama.app in System Settings → General → Login Items.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf '\n========== %s ==========\n' "$1"; }
warn() { printf '  ! %s\n' "$1"; }
ok()   { printf '  ✓ %s\n' "$1"; }
fail() { printf '  ✗ %s\n' "$1"; }

LA_DIR="$HOME/Library/LaunchAgents"
OLLAMA_PLIST="$LA_DIR/dev.forest.ollama.plist"
DAEMON_PLIST="$LA_DIR/dev.forest.daemon.plist"

bar "1. preflight: detect Ollama binary path"
OLLAMA_BIN=""
for candidate in /opt/homebrew/bin/ollama /usr/local/bin/ollama /Applications/Ollama.app/Contents/Resources/ollama; do
  if [[ -x "$candidate" ]]; then
    OLLAMA_BIN="$candidate"
    ok "found Ollama at $OLLAMA_BIN"
    break
  fi
done
if [[ -z "$OLLAMA_BIN" ]]; then
  fail "no Ollama binary found (checked /opt/homebrew, /usr/local, /Applications/Ollama.app)"
  echo ""
  echo "Install Ollama first: https://ollama.com/download"
  echo ""
  echo "Press return to close."
  read -r _
  exit 1
fi

bar "2. preflight: verify Forest venv + daemon importable"
if [[ ! -x "$HERE/.venv/bin/python" ]]; then
  fail "no .venv/bin/python — run start.command first to bootstrap"
  echo ""
  echo "Press return to close."
  read -r _
  exit 1
fi
if ! "$HERE/.venv/bin/python" -c "import forest_soul_forge.daemon.app" >/dev/null 2>&1; then
  fail "forest_soul_forge package not importable from .venv"
  echo "  Run: $HERE/.venv/bin/pip install -e ."
  echo ""
  echo "Press return to close."
  read -r _
  exit 1
fi
ok "Forest venv ok ($(.venv/bin/python --version 2>&1))"

bar "3. preflight: ensure ~/Library/LaunchAgents exists"
mkdir -p "$LA_DIR" && ok "$LA_DIR ready"

bar "4. stop currently-running Ollama (will be replaced by launchd)"
if pgrep -ix Ollama >/dev/null 2>&1; then
  osascript -e 'tell application "Ollama" to quit' 2>/dev/null
  for i in $(seq 1 10); do
    pgrep -ix Ollama >/dev/null 2>&1 || break
    sleep 1
  done
  if pgrep -ix Ollama >/dev/null 2>&1; then
    pkill -9 -ix Ollama 2>/dev/null || true
    sleep 1
  fi
  ok "Ollama.app quit"
else
  ok "Ollama.app wasn't running"
fi
if pgrep -f "ollama serve" >/dev/null 2>&1; then
  pkill -f "ollama serve" 2>/dev/null || true
  sleep 1
  ok "any standalone 'ollama serve' killed"
fi

bar "5. stop currently-running Forest daemon"
if pgrep -f "forest_soul_forge.daemon" >/dev/null 2>&1; then
  pkill -f "forest_soul_forge.daemon" 2>/dev/null || true
  sleep 1
  ok "Forest daemon killed"
else
  ok "Forest daemon wasn't running"
fi

bar "6. write Ollama plist (substituting OLLAMA_BIN)"
sed "s|@OLLAMA_BIN@|$OLLAMA_BIN|g" \
    dev-tools/launchd/dev.forest.ollama.plist.template > "$OLLAMA_PLIST"
ok "wrote $OLLAMA_PLIST"

bar "7. write Forest daemon plist"
cp dev-tools/launchd/dev.forest.daemon.plist.template "$DAEMON_PLIST"
ok "wrote $DAEMON_PLIST"

bar "8. validate plists with plutil"
if plutil -lint "$OLLAMA_PLIST" >/dev/null 2>&1; then
  ok "Ollama plist valid"
else
  fail "Ollama plist invalid — aborting"
  plutil -lint "$OLLAMA_PLIST"
  exit 1
fi
if plutil -lint "$DAEMON_PLIST" >/dev/null 2>&1; then
  ok "Forest daemon plist valid"
else
  fail "Forest daemon plist invalid — aborting"
  plutil -lint "$DAEMON_PLIST"
  exit 1
fi

bar "9. unload existing instances (if previously installed)"
launchctl bootout "gui/$(id -u)" "$OLLAMA_PLIST" 2>/dev/null && ok "unloaded prior Ollama agent" || ok "no prior Ollama agent"
launchctl bootout "gui/$(id -u)" "$DAEMON_PLIST" 2>/dev/null && ok "unloaded prior Forest daemon agent" || ok "no prior Forest daemon agent"

bar "10. bootstrap into launchd"
if launchctl bootstrap "gui/$(id -u)" "$OLLAMA_PLIST" 2>&1; then
  ok "Ollama agent bootstrapped"
else
  fail "Ollama bootstrap failed"
fi
if launchctl bootstrap "gui/$(id -u)" "$DAEMON_PLIST" 2>&1; then
  ok "Forest daemon agent bootstrapped"
else
  fail "Forest daemon bootstrap failed"
fi

bar "11. wait for both services to come up"
echo "  waiting up to 20s for Ollama API..."
for i in $(seq 1 20); do
  if curl -fsS --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama API responsive after ${i}s"
    break
  fi
  printf '.'
  sleep 1
done
echo
echo "  waiting up to 20s for Forest daemon..."
for i in $(seq 1 20); do
  if curl -fsS --max-time 1 http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
    ok "Forest daemon responsive after ${i}s"
    break
  fi
  printf '.'
  sleep 1
done
echo

bar "12. verify (launchctl list)"
echo "  launchctl list | grep forest:"
launchctl list 2>/dev/null | grep -i forest | sed 's/^/    /'

bar "13. confirm KEEP_ALIVE is in Ollama's env"
echo "  Touching qwen2.5-coder:7b to load it..."
curl -fsS --max-time 60 http://127.0.0.1:11434/api/generate \
     -H 'Content-Type: application/json' \
     -d '{"model":"qwen2.5-coder:7b","prompt":"hi","stream":false,"options":{"num_predict":1}}' \
     >/dev/null 2>&1 && ok "model touched"
echo "  ollama ps:"
ollama ps 2>/dev/null | sed 's/^/    /' || warn "ollama CLI not on PATH — that's fine, daemon is running"

bar "14. summary"
cat <<EOF
  Both daemons are now under launchd supervision:
  - Auto-start at login (RunAtLoad=true)
  - Auto-restart on crash (KeepAlive=true)
  - EnvironmentVariables baked in (KEEP_ALIVE=-1, NUM_PARALLEL=1)
  - Logs at /tmp/{ollama,forest-daemon}.{out,err}.log

  Optional next step (System Settings):
  - Energy: Prevent sleep when display off
  - Energy: Start up automatically after a power failure
  - Energy: Wake for network access

  To revert: ./uninstall-launchagents.command

  To check status: launchctl list | grep forest
  To view logs:   tail -f /tmp/forest-daemon.out.log
                  tail -f /tmp/ollama.out.log
EOF

echo ""
echo "Press return to close."
read -r _
