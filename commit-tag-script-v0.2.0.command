#!/usr/bin/env bash
# Tiny fixup: commit tag-v0.2.0.command into the repo so the working
# tree is clean for the tag-v0.2.0 script's clean-tree gate.
#
# tag-v0.2.0.command was created in Burst 64 prep but not yet committed;
# the v0.1.1 pattern was to commit the tag script as part of its own
# release prep commit. We're catching up to that pattern in a fixup.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Fixup: commit tag-v0.2.0.command ==="
echo
clean_locks
git add tag-v0.2.0.command commit-tag-script-v0.2.0.command
clean_locks
git status --short
echo
clean_locks
git commit -m "release: add tag-v0.2.0.command release script

Mirrors tag-v0.1.1.command pattern. Creates the annotated v0.2.0
tag at HEAD with release-notes message + pushes to origin. Run
after Burst 63 paperwork commit lands.

Idempotent: skips local tag creation if v0.2.0 already exists;
push step is itself idempotent."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Tag script committed. Now run tag-v0.2.0.command to create + push the tag."
echo ""
read -rp "Press Enter to close..."
