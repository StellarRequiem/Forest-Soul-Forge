#!/usr/bin/env bash
# Rebuild ONLY the frontend container against the current ./frontend/
# directory. Use when frontend code changes but the daemon doesn't need
# touching. Double-click from Finder.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf "\n=== %s ===\n" "$1"; }

bar "Rebuilding frontend image (no cache, picks up all source changes)"
docker compose build --no-cache frontend

bar "Recreating the frontend container with the fresh image"
docker compose up -d --force-recreate --no-deps frontend

bar "Health probe"
sleep 2
if curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
    echo "OK — frontend serving at http://127.0.0.1:5173/?api=http://127.0.0.1:7423"
else
    echo "WARN — frontend not responding on 5173 yet; tail the logs:"
    docker compose logs --tail=30 frontend
fi

echo ""
echo "Press return to close."
read -r _
