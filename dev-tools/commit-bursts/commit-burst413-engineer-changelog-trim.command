#!/bin/bash
# Burst 413 - Engineer-Main changelog wrapper trims diff to 14000 chars.
#
# B412's run-engineer-changelog.command pulled last-24h git log
# (29971 chars on first verify), but commit_message.v1 caps input
# at 16000 chars. Skill returned status=failed at step `cm` with
# 'bad_args: diff too long (29971 chars > 16000 max); split in
# smaller calls or summarize first'.
#
# Pragmatic fix: head -c 30000 -> head -c 14000 in the wrapper.
# 14k is well under the 16k cap so wide commits (multi-line bodies,
# co-authored, lots of files) still fit. Operator-audience changelog
# only needs the highlights; truncation drops trailing commits in
# the window which is acceptable for a daily digest.
#
# Future enhancement: split diff into per-commit chunks and run
# commit_message per chunk, then concatenate before the summarize
# step. Out of scope for this hotfix.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/run-engineer-changelog.command \
        dev-tools/commit-bursts/commit-burst413-engineer-changelog-trim.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(triune): trim Engineer-Main changelog diff to 14k chars (B413)

Burst 413. run-engineer-changelog.command pulled 29971 chars of
last-24h git log; commit_message.v1 caps input at 16000. Skill
failed at step cm with 'diff too long'.

Fix: head -c 30000 -> head -c 14000 in the wrapper. 14k leaves
margin under the 16k cap for wide commits with multi-line bodies.

Future enhancement (out of scope): split per-commit, run
commit_message per chunk, concatenate before summarize.

After landing:
  bash dev-tools/run-engineer-changelog.command
  Expected: status=succeeded; lineage memory gains a
  commit_changelog outcome."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 413 complete - engineer-changelog trim ==="
echo "=========================================================="
echo "Press any key to close."
read -n 1 || true
