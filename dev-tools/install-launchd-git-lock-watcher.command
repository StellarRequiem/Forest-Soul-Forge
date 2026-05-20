#!/usr/bin/env bash
# B450 — idempotent installer for dev.forest.git-lock-watcher.plist.
# Mirrors the B439/B441/B442 pattern. Bails clean if already
# bootstrapped.
#
# Purpose: closes the §5 sandbox-index-lock race once and for all by
# auto-removing stale .git/index.lock files. After install: sandbox
# git ops can leak locks freely; the watcher cleans them up within
# ~15 seconds.

set -uo pipefail

LABEL="dev.forest.git-lock-watcher"
TEMPLATE="/Users/llm01/Forest-Soul-Forge/dev-tools/launchd/${LABEL}.plist.template"
TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"

echo "==========================================================="
echo "Install: ${LABEL}.plist"
echo "==========================================================="
echo "  template: $TEMPLATE"
echo "  target:   $TARGET"
echo "  user:     $UID_NUM ($USER)"
echo "  fires on: WatchPaths .git/index.lock change"
echo

if [ ! -f "$TEMPLATE" ]; then
  echo "ERROR: template missing. Aborting."
  exit 1
fi

if launchctl print "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1; then
  echo "Already loaded:"
  launchctl print "gui/${UID_NUM}/${LABEL}" | grep -E "(state|standard out path)" | head -3
  echo
  echo "To reload: launchctl bootout gui/${UID_NUM}/${LABEL} && re-run."
  echo
  echo "Press any key to close."
  read -n 1 || true
  exit 0
fi

mkdir -p "$(dirname "$TARGET")"
cp "$TEMPLATE" "$TARGET"
chmod 600 "$TARGET"
echo "Copied plist to $TARGET"
echo

launchctl bootstrap "gui/${UID_NUM}" "$TARGET" || { echo "bootstrap failed"; exit 1; }
echo "Bootstrapped."
echo

echo "Post-install state:"
launchctl print "gui/${UID_NUM}/${LABEL}" | grep -E "(state|watch paths|standard out path|standard error path)" | head -6
echo
echo "Watcher log: /Users/llm01/Forest-Soul-Forge/.run/git-lock-watcher.log"
echo
echo "Press any key to close."
read -n 1 || true
