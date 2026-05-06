#!/usr/bin/env bash
# One-shot freer of background-app RAM before tuning Ollama priority.
#
# Quits Spotify, Discord, and Docker Desktop via AppleScript's gentle
# `quit app` — preserves user state (Spotify queue, Discord login,
# Docker volumes) and lets each app run its own cleanup. Not force-quit.
#
# Frees roughly:
#   Docker VM Service  ~4.88 GB  (across 2 processes — kills the
#                                 fsf-ollama container too; native
#                                 Ollama on 11434 is unaffected)
#   Spotify + Helpers  ~786 MB
#   Discord + Helper   ~520 MB
#   Total              ~6.2 GB
#
# Re-run anytime. Safe — gentle quit, no force.

set -uo pipefail

bar() { printf '\n========== %s ==========\n' "$1"; }

quit_app() {
  local name="$1"
  if pgrep -ix "$name" >/dev/null 2>&1; then
    osascript -e "tell application \"$name\" to quit" 2>/dev/null \
      && echo "  ✓ quit signal sent to $name" \
      || echo "  ✗ failed to quit $name"
  else
    echo "  - $name not running"
  fi
}

bar "1. before"
ps aux | awk '{rss+=$6} END {printf "  total RSS: %.2f GB\n", rss/1024/1024}'

bar "2. quitting"
quit_app "Spotify"
quit_app "Discord"
quit_app "Docker Desktop"
quit_app "Docker"

bar "3. waiting 5s for graceful shutdown"
sleep 5

bar "4. survivors (anything still running?)"
for app in Spotify Discord "Docker Desktop"; do
  if pgrep -ix "$app" >/dev/null 2>&1; then
    echo "  ! $app still running — try again or quit manually"
  else
    echo "  ✓ $app gone"
  fi
done

# Docker has helper processes that linger. Check for VM Service.
if pgrep -f "com.docker.virtualization" >/dev/null 2>&1; then
  echo "  ! Docker VM Service still running"
else
  echo "  ✓ Docker VM Service gone"
fi

bar "5. after"
ps aux | awk '{rss+=$6} END {printf "  total RSS: %.2f GB\n", rss/1024/1024}'

echo ""
echo "Done. Press return to close."
read -r _
