#!/usr/bin/env bash
# Push B449 + capture output to a sandbox-readable location.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.."

OUT=".run/push-b449.log"
{
  echo "=== $(date) — push attempt ==="
  echo "HEAD: $(git rev-parse --short HEAD)"
  echo "origin/main: $(git rev-parse --short origin/main)"
  echo
  git push origin main 2>&1
  echo "exit: $?"
  echo "=== DONE ==="
} > "$OUT" 2>&1

cat "$OUT"
echo
echo "Press any key to close."
read -n 1 || true
