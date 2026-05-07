#!/bin/bash
# One-shot fix: setup-anthropic-frontier.command wrote
# FSF_FRONTIER_BASE_URL=https://api.anthropic.com/v1 to .env,
# but the FrontierProvider appends /v1/chat/completions — so the
# real URL would have been .../v1/v1/chat/completions (404).
#
# This script rewrites the .env entry to drop the trailing /v1
# and restarts the daemon so the new value takes effect.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: no .env at $ENV_FILE"
  exit 1
fi

TS=$(date +%Y%m%d-%H%M%S)
cp "$ENV_FILE" "$ENV_FILE.bak-frontier-fix-$TS"
echo "[1/3] Backed up .env -> .env.bak-frontier-fix-$TS"

# macOS sed requires the empty argument to -i.
sed -i.tmpbak \
  's|^FSF_FRONTIER_BASE_URL=https://api.anthropic.com/v1$|FSF_FRONTIER_BASE_URL=https://api.anthropic.com|' \
  "$ENV_FILE"
rm -f "$ENV_FILE.tmpbak"
echo "[2/3] Patched FSF_FRONTIER_BASE_URL -> https://api.anthropic.com"

echo "      Current frontier env in .env:"
grep "^FSF_FRONTIER" "$ENV_FILE" | sed 's/=.*$/=<...>/'

PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "[3/3] Restarted ${PLIST_LABEL}"
  sleep 5
else
  echo "[3/3] WARN: ${PLIST_LABEL} not registered with launchd."
  echo "      Restart the daemon by hand to pick up the new base_url."
fi

echo
echo "Fix complete. Smoke test still works (it hardcodes the right"
echo "URL). Next dispatches via the running daemon will now use"
echo "the correct endpoint."
echo
echo "Press any key to close this window."
read -n 1
