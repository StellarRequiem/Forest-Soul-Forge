#!/usr/bin/env bash
# Kickstart the Forest daemon launchd job so it picks up code
# changes from the latest pull. Single-purpose — no scheduler /
# task / breaker side effects (those live in
# fix-bug1-restart-and-reset.command).
#
# Created during the 2026-05-06 e2e-test run after B154-B174
# shipped — needed a focused restart command to verify the
# daemon was running the latest code path.

set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

if [[ -f dev-tools/_fsf-env.sh ]]; then source dev-tools/_fsf-env.sh; fi

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. kickstart Forest daemon"
launchctl kickstart -k "gui/$(id -u)/dev.forest.daemon" \
  && echo "  ✓ kickstart -k sent" \
  || echo "  ✗ kickstart failed (is the launchd job loaded?)"

bar "2. wait up to 30s for /healthz"
for i in $(seq 1 30); do
  if curl -fsS --max-time 1 http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
    echo "  ✓ daemon back up after ${i}s"
    break
  fi
  printf '.'
  sleep 1
done
echo

bar "3. probe a B173 endpoint to confirm latest code is loaded"
resp=$(curl -fsS http://127.0.0.1:7423/secrets/backend 2>/dev/null)
if [[ -n "$resp" ]]; then
  echo "  ✓ /secrets/backend responded: $resp"
else
  echo "  ? /secrets/backend not reachable yet — daemon may still be loading"
fi

bar "4. show current commit"
git --no-pager log --oneline -1 2>/dev/null

echo ""
echo "Done. Press return to close."
read -r _
