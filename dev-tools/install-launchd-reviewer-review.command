#!/usr/bin/env bash
# One-off: install dev.forest.reviewer-review launchd plist for the
# weekly Monday 8am Reviewer-Main cadence.
#
# Mirrors install-launchd-wiring-audit.command (B439). Same idempotent
# pattern: bails clean if already bootstrapped.
#
# Pre-req: Reviewer-Main agent must exist + be active. As of B429,
# sibling-3 code_reviewer_8808e39f43ac_3 is the active reviewer with
# allowed_paths configured for Option C scope. The runner script
# (dev-tools/run-reviewer-review.command) finds the active reviewer
# by role lookup, so this just needs SOME reviewer alive.

set -uo pipefail

LABEL="dev.forest.reviewer-review"
TEMPLATE="/Users/llm01/Forest-Soul-Forge/dev-tools/launchd/${LABEL}.plist.template"
TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"

echo "==========================================================="
echo "Install: ${LABEL}.plist"
echo "==========================================================="
echo "  template: $TEMPLATE"
echo "  target:   $TARGET"
echo "  user:     $UID_NUM ($USER)"
echo "  cadence:  Monday 8:00am local"
echo

if [ ! -f "$TEMPLATE" ]; then
  echo "ERROR: template missing. Aborting."
  exit 1
fi

# Check already bootstrapped.
if launchctl print "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1; then
  echo "Already loaded:"
  launchctl print "gui/${UID_NUM}/${LABEL}" | grep -E "(state|next.start|standard out path)" | head -4
  echo
  echo "If you want to reload after changing the template:"
  echo "  launchctl bootout gui/${UID_NUM}/${LABEL}"
  echo "  re-run this script."
  echo
  echo "Press any key to close."
  read -n 1 || true
  exit 0
fi

# Copy template → target (template has no placeholders to substitute).
mkdir -p "$(dirname "$TARGET")"
cp "$TEMPLATE" "$TARGET"
chmod 600 "$TARGET"
echo "Copied plist to $TARGET"
echo

# Bootstrap into the user's GUI launch domain.
launchctl bootstrap "gui/${UID_NUM}" "$TARGET" || { echo "bootstrap failed"; exit 1; }
echo "Bootstrapped."
echo

# Verify.
echo "Post-install state:"
launchctl print "gui/${UID_NUM}/${LABEL}" | grep -E "(state|next.start|standard out path|standard error path)" | head -6
echo
echo "Next tick: next Monday 8:00am local time."
echo "For an ad-hoc run NOW:"
echo "  bash /Users/llm01/Forest-Soul-Forge/dev-tools/run-reviewer-review.command"
echo
echo "Logs: /tmp/forest-reviewer-review.{out,err}.log"
echo
echo "Press any key to close."
read -n 1 || true
