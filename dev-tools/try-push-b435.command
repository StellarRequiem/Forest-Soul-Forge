#!/usr/bin/env bash
# Diagnostic: attempt push of B435 + capture exact output so we can
# diagnose any remote rejection. Writes to /tmp/b435-push-attempt.log
# in addition to printing on-screen.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.."

LOG=/tmp/b435-push-attempt.log

echo "==========================================================="
echo "B435 push attempt + capture"
echo "==========================================================="
echo

{
  echo "=== HEAD ==="
  git log --format='%h %G? %s' -1
  echo
  echo "=== Show signature on HEAD ==="
  git log --show-signature -1 2>&1 | head -10
  echo
  echo "=== Remote tracking ==="
  git rev-parse --short HEAD origin/main
  echo
  echo "=== Push attempt ==="
  git push origin main 2>&1
  PUSH_RC=$?
  echo
  echo "Push exit code: $PUSH_RC"
} | tee "$LOG"

echo
echo "Full output saved to: $LOG"
echo
echo "Press any key to close."
read -n 1 || true
