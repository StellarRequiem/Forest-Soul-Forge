#!/usr/bin/env bash
# Burst 439 — commit the launchd installer + record the
# all-green-harness milestone.
#
# Adds dev-tools/install-launchd-wiring-audit.command, the one-off
# helper that copies the wiring-audit plist template to
# ~/Library/LaunchAgents/ and runs launchctl bootstrap. Idempotent;
# bails clean if already installed.
#
# Context: this session
#   - Birthed WiringSentinel-D5 (instance_id wiring_sentinel_994a714df156)
#     via dev-tools/birth-wiring-sentinel.command (B438 era).
#   - Installed dev.forest.wiring-audit.plist into the user GUI
#     launch domain via the new installer.
#   - Confirmed 15/15 PASS on diagnostic-all (orphan_count=0 for
#     the first time since the harness was instrumented).
#
# Pair with the existing daemon + ollama plists that have been
# bootstrapped since 2026-05-11; this completes the substrate side
# of the 24/7 ops recipe per user_hardware_and_24_7_ops memory.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: helper was untracked; next operator who wants to
#     install the cadence on a fresh machine has no procedure;
#     reinstall on this machine has no record.
#   Prove non-load-bearing: dev-tools script; no schema or routes.
#   Prove alternative: leave untracked (rejected; operator
#     procedures belong in repo per CLAUDE.md operating principles).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 439 — launchd wiring-audit installer commit"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add dev-tools/install-launchd-wiring-audit.command
git add dev-tools/commit-bursts/commit-burst439-launchd-wiring-audit-installer.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "chore(ops): launchd wiring-audit installer + first 15/15 harness green (B439)

Adds dev-tools/install-launchd-wiring-audit.command, the one-off
helper used in this session to install dev.forest.wiring-audit.plist
into ~/Library/LaunchAgents/ via launchctl bootstrap. Idempotent —
bails clean if already bootstrapped.

Pairs with the existing dev.forest.daemon + dev.forest.ollama plists
that have been live since 2026-05-11; this completes the substrate
side of the 24/7-ops launchd setup per user_hardware_and_24_7_ops.

Verification milestone in this session:
  * WiringSentinel-D5 born: wiring_sentinel_994a714df156 / green.
  * dev.forest.wiring-audit.plist bootstrapped into user GUI launch
    domain. 4-hour cadence (StartInterval=14400). First tick within
    4 hours of install.
  * diagnostic-all reports 15 PASS / 0 FAIL — the first fully-green
    harness run since instrumentation. Section-15 orphan_count=0
    after B437/B438 closed the 3 orphan-tool surface.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: helper was untracked; future installs lacked the
    procedure-of-record.
  Prove non-load-bearing for kernel: dev-tools script only.
  Prove alternative: leaving untracked rejected per CLAUDE.md
    operating principles (audit-grade procedures belong in repo)." || { echo "commit failed"; exit 1; }

echo
echo "==========================================================="
echo "Post-commit signature status:"
echo "==========================================================="
git log --format='%h %G? %s' -4

echo
echo "Pushing..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B439 pushed."
echo
echo "Press any key to close."
read -n 1 || true
