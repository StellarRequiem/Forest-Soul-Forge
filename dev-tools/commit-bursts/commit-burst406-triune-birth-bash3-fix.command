#!/bin/bash
# Burst 406 - Triune-Main birth script bash-3.2 compat fix.
#
# birth-triune-main.command (B405) used ${name,,} for lowercase
# conversion — bash 4+ syntax. macOS ships bash 3.2 at /bin/bash
# (Apple won't ship GPLv3). Script bombed:
#   line 44: birth-${name,,}: bad substitution
#
# Replace with POSIX-compatible `echo | tr [:upper:] [:lower:]`.
# Two call sites updated.
#
# Also valid: switch shebang to /usr/bin/env bash (often newer bash
# via Homebrew at /opt/homebrew/bin/bash), but that adds an
# environment dependency. tr is universal. Tr it is.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/birth-triune-main.command \
        dev-tools/commit-bursts/commit-burst406-triune-birth-bash3-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(triune): birth-triune-main bash-3.2 compat (B406)

Burst 406. B405 used \${name,,} (bash 4+ lowercase) but macOS
ships bash 3.2 at /bin/bash (no GPLv3 from Apple). Script bombed
at line 44: 'birth-\${name,,}: bad substitution'.

Replace with: \$(echo \"\$name\" | tr '[:upper:]' '[:lower:]').
POSIX-compatible. Two call sites (idempotency key + posture key).

Verified with: bash -n dev-tools/birth-triune-main.command
returns 'syntax OK'.

After landing: bash dev-tools/birth-triune-main.command should
produce three named agents (Engineer-Main + Reviewer-Main +
Architect-Main)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 406 complete - bash-3.2 compat fix ==="
echo "=========================================================="
echo "Next: bash dev-tools/birth-triune-main.command"
echo ""
echo "Press any key to close."
read -n 1 || true
