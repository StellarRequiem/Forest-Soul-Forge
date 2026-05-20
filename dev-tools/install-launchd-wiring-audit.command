#!/usr/bin/env bash
# One-off: install dev.forest.wiring-audit launchd plist for the
# 4-hour WiringSentinel cadence (ADR-0081 D7).
#
# Pre-req: dev-tools/birth-wiring-sentinel.command must have been
# run successfully so a WiringSentinel-D5 agent exists. Otherwise
# the scheduled run-wiring-audit.command exits non-zero every
# 4 hours (cosmetic — retries on next tick).
#
# Idempotent: if the plist is already installed, this script does
# nothing destructive. Bootout-then-bootstrap if you need to refresh.

set -uo pipefail

LABEL="dev.forest.wiring-audit"
TEMPLATE="/Users/llm01/Forest-Soul-Forge/dev-tools/launchd/${LABEL}.plist.template"
TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"

echo "==========================================================="
echo "Install: ${LABEL}.plist"
echo "==========================================================="
echo "  template: $TEMPLATE"
echo "  target:   $TARGET"
echo "  user:     $UID_NUM ($USER)"
echo

if [ ! -f "$TEMPLATE" ]; then
  echo "ERROR: template missing. Aborting."
  exit 1
fi

# Check if already bootstrapped — bail clean if so.
if launchctl print "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1; then
  echo "Already loaded:"
  launchctl print "gui/${UID_NUM}/${LABEL}" | grep -E "(state|last exit)" | head -4
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
launchctl bootstrap "gui/${UID_NUM}" "$TARGET" || { echo "bootstrap failed (check log above; common: 5: Input/output error if Forest's daemon is unhealthy)"; exit 1; }
echo "Bootstrapped."
echo

# Verify.
echo "Post-install state:"
launchctl print "gui/${UID_NUM}/${LABEL}" | grep -E "(state|run.interval|standard out path|standard error path)" | head -6
echo
echo "Next tick lands within 4 hours (14400s). For an ad-hoc run:"
echo "  bash /Users/llm01/Forest-Soul-Forge/dev-tools/run-wiring-audit.command"
echo
echo "Press any key to close."
read -n 1 || true
