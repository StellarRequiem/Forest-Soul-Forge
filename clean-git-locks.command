#!/usr/bin/env bash
# Clean up sandbox-orphaned .git lock files. One-shot.
set -uo pipefail
cd "$(dirname "$0")"

echo "[clean] removing .git/index.lock if present..."
rm -f .git/index.lock 2>/dev/null && echo "  ✓ index.lock cleared" || echo "  (no index.lock to clean)"

echo "[clean] removing .git/HEAD.lock if present..."
rm -f .git/HEAD.lock 2>/dev/null && echo "  ✓ HEAD.lock cleared" || echo "  (no HEAD.lock to clean)"

echo "[clean] removing orphan tmp_obj_* files..."
N=$(find .git/objects -name 'tmp_obj_*' -type f 2>/dev/null | wc -l | tr -d ' ')
if [ "$N" -gt 0 ]; then
  find .git/objects -name 'tmp_obj_*' -type f -delete
  echo "  ✓ removed $N orphan tmp_obj_*"
else
  echo "  (no tmp_obj_* to clean)"
fi

echo ""
echo "[clean] git status:"
git status --short
echo ""
echo "[clean] git log -3:"
git log --oneline -3
echo ""
echo "Press return to close."
read -r _
