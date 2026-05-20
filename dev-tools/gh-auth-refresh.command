#!/usr/bin/env bash
# One-off: rotate the gh CLI OAuth token bound to the GitHub CLI
# authorized app. Triggered by ADR-0084 Tier 1 hardening (B430-B433
# operator-side follow-up).
#
# gh will open a browser for device-code OAuth; complete it in the
# Chrome window that pops open. After this, the OAuth grant for
# "GitHub CLI" rotates and any cached push credentials refresh.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.."

echo "=========================================================="
echo "gh auth refresh — rotate OAuth token (scopes: repo,workflow)"
echo "=========================================================="
echo

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh CLI not in PATH. Install via 'brew install gh' or check"
  echo "       https://cli.github.com/. Aborting."
  exit 1
fi

echo "Current auth status BEFORE refresh:"
gh auth status 2>&1 || true
echo

echo "Running: gh auth refresh -h github.com -s repo,workflow"
echo "(follow the prompts in the terminal + browser)"
echo
gh auth refresh -h github.com -s repo,workflow
RC=$?
echo
echo "Refresh exit code: $RC"
echo

echo "Auth status AFTER refresh:"
gh auth status 2>&1 || true
echo
echo "=========================================================="
echo "Done. Close this window when finished."
echo "=========================================================="
