#!/bin/bash
# cleanup-bak-files.command — delete the .py.bak / .bak files that
# Edit-tool operations leave behind on the host filesystem.
#
# Why this exists: the Cowork sandbox writes .bak snapshots when
# Edit operations modify a file, and once those .bak files land
# on the host mount they pick up host-level permissions the sandbox
# can't override (rm returns "Operation not permitted"). The
# operator (host user) has to delete them.
#
# Pattern surfaced repeatedly through Bursts 261-269: the
# encryption-at-rest arc touched many files via Edit, and the
# resulting .bak files accumulated in the working tree.
# B270 adds *.bak / *.py.bak to .gitignore so they stop showing
# in git status; this script clears the ones already on disk.
#
# Safe to run any time — no-op if no .bak files exist. Excludes
# .venv (third-party-installed) and .git internal state.

set -euo pipefail
cd "$(dirname "$0")"

echo "=== scanning for .bak files in working tree ==="

BAK_FILES=$(find . \
  -name "*.bak" \
  -not -path "./.venv/*" \
  -not -path "./.git/*" \
  -not -path "./node_modules/*" \
  -not -path "./frontend/node_modules/*" \
  2>/dev/null)

if [ -z "$BAK_FILES" ]; then
  echo "no .bak files found — working tree is already clean"
  echo ""
  echo "Press any key to close."
  read -n 1
  exit 0
fi

COUNT=$(echo "$BAK_FILES" | wc -l | tr -d ' ')
echo "found $COUNT .bak file(s):"
echo "$BAK_FILES"
echo ""
echo "=== deleting ==="

echo "$BAK_FILES" | while read -r f; do
  if [ -f "$f" ]; then
    rm -v "$f"
  fi
done

echo ""
echo "=== done — $COUNT file(s) removed ==="
echo "Press any key to close."
read -n 1
