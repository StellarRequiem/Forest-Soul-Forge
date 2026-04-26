#!/usr/bin/env bash
# Rebuild BOTH daemon and frontend containers against current source.
# Use after a feature branch lands (or when /healthz reports stale schema,
# or when the frontend dropdown shows roles your latest commit added).
# Double-click from Finder.
#
# Why this exists separate from docker-up.command:
#   docker-up.command brings the stack up but skips a no-cache rebuild
#   when the image already exists. After source changes, you want
#   --no-cache to make sure the new files actually land in the image.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf "\n=== %s ===\n" "$1"; }

bar "Rebuilding daemon image (no cache)"
docker compose build --no-cache daemon

bar "Rebuilding frontend image (no cache)"
docker compose build --no-cache frontend

bar "Recreating both containers with the fresh images"
docker compose up -d --force-recreate --no-deps daemon frontend

bar "Health probe — waiting up to 30s for daemon /healthz"
ok=false
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
        ok=true; break
    fi
    sleep 1
done
if $ok; then
    echo "OK — daemon /healthz responded."
else
    echo "WARN — daemon didn't respond. Last 30 log lines:"
    docker compose logs --tail=30 daemon
fi

bar "Smoke check — /tools/catalog (should list tools added in T4)"
if curl -fsS http://127.0.0.1:7423/tools/catalog 2>/dev/null | grep -q '"version"'; then
    echo "OK — /tools/catalog responding."
else
    echo "WARN — /tools/catalog NOT responding. The T4 router didn't land."
fi

bar "Frontend probe"
if curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
    echo "OK — frontend at http://127.0.0.1:5173/?api=http://127.0.0.1:7423"
else
    echo "WARN — frontend not responding."
fi

echo ""
echo "Press return to close."
read -r _
