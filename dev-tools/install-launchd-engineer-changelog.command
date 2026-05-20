#!/usr/bin/env bash
# One-off: install dev.forest.engineer-changelog launchd plist for the
# daily 6am Engineer-Main commit_changelog cadence.
#
# Daily 6am cadence reads the last 24h of git log + diff stat,
# dispatches commit_changelog.v1 on Engineer-Main. Output lands in
# Engineer-Main's lineage memory. Operator can grep for the daily
# changelog entry the next morning.
#
# Pre-req: Engineer-Main agent must exist + be active. Per B429
# memory, Engineer-Main = software_engineer_c1be854eadef_2 (yellow).
# Runner script looks up the active engineer by role.
#
# Idempotent: bails clean if already bootstrapped.

set -uo pipefail

LABEL="dev.forest.engineer-changelog"
TEMPLATE="/Users/llm01/Forest-Soul-Forge/dev-tools/launchd/${LABEL}.plist.template"
TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"

echo "==========================================================="
echo "Install: ${LABEL}.plist"
echo "==========================================================="
echo "  template: $TEMPLATE"
echo "  target:   $TARGET"
echo "  user:     $UID_NUM ($USER)"
echo "  cadence:  daily 6:00am local"
echo

if [ ! -f "$TEMPLATE" ]; then
  echo "ERROR: template missing. Aborting."
  exit 1
fi

if launchctl print "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1; then
  echo "Already loaded:"
  launchctl print "gui/${UID_NUM}/${LABEL}" | grep -E "(state|next.start)" | head -2
  echo
  echo "To reload: launchctl bootout gui/${UID_NUM}/${LABEL} && re-run."
  echo
  echo "Press any key to close."
  read -n 1 || true
  exit 0
fi

mkdir -p "$(dirname "$TARGET")"
cp "$TEMPLATE" "$TARGET"
chmod 600 "$TARGET"
echo "Copied plist to $TARGET"
echo

launchctl bootstrap "gui/${UID_NUM}" "$TARGET" || { echo "bootstrap failed"; exit 1; }
echo "Bootstrapped."
echo

echo "Post-install state:"
launchctl print "gui/${UID_NUM}/${LABEL}" | grep -E "(state|next.start|standard out path|standard error path)" | head -6
echo
echo "Next tick: tomorrow 6:00am local time."
echo "Ad-hoc run: bash /Users/llm01/Forest-Soul-Forge/dev-tools/run-engineer-changelog.command"
echo "Logs: /tmp/forest-engineer-changelog.{out,err}.log"
echo
echo "Press any key to close."
read -n 1 || true
