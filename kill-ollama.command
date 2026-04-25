#!/usr/bin/env bash
# Throwaway helper — kills whatever non-Docker process is holding port
# 11434 (typically the lingering Ollama background daemon installed by
# the Mac app's launchd plist). Double-click from Finder.
#
# Safe because: if no Docker fsf-ollama container is running yet (which
# is our case — it failed to bind), the only listener on 11434 is the
# offending native Ollama. We don't touch Docker's port-forwarding
# process which lives in com.docker.backend.
set -euo pipefail

bar() { printf '\n=== %s ===\n' "$1"; }

bar "1. who is listening on 11434?"
lsof -nP -iTCP:11434 -sTCP:LISTEN || echo "(nobody — port already free)"

bar "2. show all ollama-related processes (parent app + daemon)"
ps -axo pid,ppid,comm,args | grep -iE 'ollama|Ollama\.app' | grep -v grep || echo "(none)"

bar "3. kill Ollama.app parent process (stops the supervisor)"
# Kill the .app first so it can't respawn the daemon. Match by full
# path so we don't accidentally hit the docker fsf-ollama container
# (those processes live inside a container and don't appear in host ps
# with that path anyway, but be paranoid).
pkill -9 -f "Ollama.app/Contents/MacOS/Ollama" || true
pkill -9 -f "Applications/Ollama.app" || true
sleep 1

bar "4. kill any remaining ollama daemon"
pkill -9 -x ollama || true
PIDS="$(lsof -nP -iTCP:11434 -sTCP:LISTEN -t || true)"
if [ -n "${PIDS:-}" ]; then
    echo "still listening — kill -9 PIDs: $PIDS"
    # shellcheck disable=SC2086
    kill -9 $PIDS
    sleep 1
fi

bar "5. stop Homebrew-managed ollama service (the actual respawner)"
# Homebrew installs ollama as a launchd-managed service. The plist
# lives at ~/Library/LaunchAgents/homebrew.mxcl.ollama.plist and will
# respawn `ollama serve` after every kill until we boot it out.
if command -v brew >/dev/null 2>&1; then
    brew services stop ollama 2>/dev/null \
        && echo "brew services stop ollama — OK" \
        || echo "brew services stop ollama — already stopped or not installed"
fi

bar "6. bootout any launchd entry matching 'ollama'"
# Belt-and-braces: directly bootout matching plists in case brew
# services didn't cover it (Mac App Store version, sideloaded, etc).
for PLIST in \
    "$HOME/Library/LaunchAgents/homebrew.mxcl.ollama.plist" \
    "$HOME/Library/LaunchAgents/com.ollama.helper.plist" \
    "$HOME/Library/LaunchAgents/com.ollama.ollama.plist" \
    "$HOME/Library/LaunchAgents/com.electron.ollama.plist" \
    "/Library/LaunchAgents/com.ollama.helper.plist" \
    "/Library/LaunchDaemons/com.ollama.helper.plist"; do
    if [ -f "$PLIST" ]; then
        launchctl bootout "user/$(id -u)" "$PLIST" 2>/dev/null \
            || launchctl bootout "system" "$PLIST" 2>/dev/null \
            || true
        echo "attempted unload: $PLIST"
    fi
done

bar "7. kill again now that supervisor is down"
sleep 1
PIDS="$(lsof -nP -iTCP:11434 -sTCP:LISTEN -t || true)"
if [ -n "${PIDS:-}" ]; then
    echo "final kill: $PIDS"
    # shellcheck disable=SC2086
    kill -9 $PIDS
    sleep 1
fi

# Confirm what's left in launchd
echo "remaining launchd entries matching 'ollama':"
launchctl list | grep -i ollama || echo "(none — supervisor is gone)"

bar "8. verify"
sleep 1
if lsof -nP -iTCP:11434 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "WARNING: something is STILL listening on 11434:"
    lsof -nP -iTCP:11434 -sTCP:LISTEN
    echo "you may need to disable Ollama's 'Open at Login' setting and reboot."
else
    echo "port 11434 is FREE — safe to start fsf-ollama now."
fi

echo
echo "Press return to close."
read -r _
