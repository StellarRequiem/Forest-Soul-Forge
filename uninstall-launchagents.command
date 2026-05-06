#!/usr/bin/env bash
# Uninstall Forest's launchd agents.
#
# Removes both LaunchAgents from launchd (so they stop at next reboot
# AND immediately) and deletes the plist files. Does NOT re-enable
# Ollama.app's auto-launch — if you want that, re-enable it manually
# in System Settings → General → Login Items.

set -uo pipefail

bar() { printf '\n========== %s ==========\n' "$1"; }
ok()   { printf '  ✓ %s\n' "$1"; }
warn() { printf '  ! %s\n' "$1"; }

LA_DIR="$HOME/Library/LaunchAgents"
OLLAMA_PLIST="$LA_DIR/dev.forest.ollama.plist"
DAEMON_PLIST="$LA_DIR/dev.forest.daemon.plist"

bar "1. unload from launchd"
if launchctl bootout "gui/$(id -u)" "$OLLAMA_PLIST" 2>/dev/null; then
  ok "Ollama agent unloaded (and process killed)"
else
  warn "Ollama agent wasn't loaded"
fi
if launchctl bootout "gui/$(id -u)" "$DAEMON_PLIST" 2>/dev/null; then
  ok "Forest daemon agent unloaded (and process killed)"
else
  warn "Forest daemon agent wasn't loaded"
fi

bar "2. delete plist files"
[[ -f "$OLLAMA_PLIST" ]] && rm -f "$OLLAMA_PLIST" && ok "removed $OLLAMA_PLIST" || ok "no Ollama plist to remove"
[[ -f "$DAEMON_PLIST" ]] && rm -f "$DAEMON_PLIST" && ok "removed $DAEMON_PLIST" || ok "no Forest daemon plist to remove"

bar "3. verify nothing left"
remaining=$(launchctl list 2>/dev/null | grep -i forest || true)
if [[ -z "$remaining" ]]; then
  ok "no launchd agents matching 'forest' remain"
else
  warn "still see in launchctl list:"
  echo "$remaining" | sed 's/^/    /'
fi

bar "4. notes"
cat <<'EOF'
  The launchd agents are gone. Ollama and Forest daemon are stopped.

  To use Forest manually again:
    - Ollama: open /Applications/Ollama.app  (or `ollama serve` from a shell)
    - Forest: ./run.command  (or ./start.command for a fresh bootstrap)

  To re-enable Ollama.app at login:
    System Settings → General → Login Items & Extensions → click +
    → add /Applications/Ollama.app

  /tmp/{ollama,forest-daemon}.{out,err}.log are preserved if you
  want to grep for crash reasons. Delete them manually if not needed.
EOF

echo ""
echo "Press return to close."
read -r _
