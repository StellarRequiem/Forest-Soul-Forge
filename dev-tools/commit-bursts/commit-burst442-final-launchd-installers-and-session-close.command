#!/usr/bin/env bash
# Burst 442 — final 2 launchd installers, closing the 6-plist inventory.
#
# Adds:
#   * dev-tools/install-launchd-engineer-changelog.command — daily 6am
#     Engineer-Main commit_changelog cadence. Reads last 24h of git
#     log + diff stat, dispatches commit_changelog.v1, lands the
#     summary in Engineer-Main's lineage memory.
#   * dev-tools/install-launchd-triune-triage.command — daily 7am
#     Triune-Main wiring_audit_triage cadence. Fires
#     wiring_audit_triage.v1 against Engineer-Main; the skill
#     delegates extract → rank → synthesize across the triune
#     (Engineer + Reviewer + Architect siblings).
#
# Both follow the B439/B441 idempotent installer pattern. With
# B442 the launchd inventory is 6 of 6 live:
#   dev.forest.daemon              KeepAlive + RunAtLoad (2026-05-11)
#   dev.forest.ollama              KeepAlive + RunAtLoad (2026-05-11)
#   dev.forest.wiring-audit        every 4hr             (B439 today)
#   dev.forest.reviewer-review     Monday 8:00am         (B441 today)
#   dev.forest.engineer-changelog  daily 6:00am          (B442 today)
#   dev.forest.triune-triage       daily 7:00am          (B442 today)
#
# That completes the substrate (auto-start) + scheduled-agent-work
# layers of the 24/7 ops recipe per user_hardware_and_24_7_ops memory.
#
# Closes the 2026-05-20 single-day arc that started with the post-B434
# diagnostic baseline and runs through B442. Eight commits total (B435
# unsigned race + B436-B442 signed). Substrate is in its cleanest state
# since instrumentation: 15/15 diagnostic green, orphan_count=0,
# CLAUDE.md sec1-sec6 codified, ADR-0084 Tier 1 hardening LIVE.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: 2 cadences uninstalled means engineer-changelog
#     summaries don't accrue + triune-triage punch list doesn't
#     surface; manual ad-hoc runs would be the alternative.
#   Prove non-load-bearing for kernel: dev-tools/ scripts only.
#     No schema, no events, no routes.
#   Prove alternative: leave uninstalled (rejected; the templates
#     have existed for sessions; install closes the queue).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 442 — final 2 launchd installers + session close"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add dev-tools/install-launchd-engineer-changelog.command
git add dev-tools/install-launchd-triune-triage.command
git add dev-tools/commit-bursts/commit-burst442-final-launchd-installers-and-session-close.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "chore(ops): final 2 launchd installers — 6 of 6 plists live (B442)

Adds the last two idempotent installer scripts:
  * dev-tools/install-launchd-engineer-changelog.command —
    daily 6am Engineer-Main commit_changelog cadence
  * dev-tools/install-launchd-triune-triage.command —
    daily 7am Triune-Main wiring_audit_triage cadence

Both follow the B439/B441 pattern. With this commit landed the
launchd plist inventory is 6 of 6 live:

  dev.forest.daemon              KeepAlive + RunAtLoad (since 2026-05-11)
  dev.forest.ollama              KeepAlive + RunAtLoad (since 2026-05-11)
  dev.forest.wiring-audit        every 4hr             (B439 today)
  dev.forest.reviewer-review     Monday 8:00am         (B441 today)
  dev.forest.engineer-changelog  daily 6:00am          (B442 today)
  dev.forest.triune-triage       daily 7:00am          (B442 today)

Substrate (auto-start) + scheduled-agent-work layers of the 24/7
ops recipe per user_hardware_and_24_7_ops are now complete.

This closes the 2026-05-20 single-day arc. Eight commits total:
B435 (unsigned race) + B436-B442 signed. Substrate is in its
cleanest state since instrumentation:
  * 15/15 diagnostic-all green (first time)
  * section-15 orphan_count=0
  * commit chain signed-since-B436
  * ADR-0084 Tier 1 hardening LIVE per all 6 rules
  * CLAUDE.md sec0-sec6 codified (sec4-sec6 added today)
  * substrate-perf benchmark baseline captured (B440)

Memory checkpoint: project_2026_05_20_full_day_arc.md captures
the full 8-commit timeline + load-bearing lessons + per-substrate
before/after state. Future sessions read that file to orient
without spelunking individual commit bodies.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 2 cadences uninstalled means engineer-changelog
    summaries don't accrue + triune-triage punch list doesn't
    surface; manual ad-hoc runs would be the alternative.
  Prove non-load-bearing: dev-tools scripts only.
  Prove alternative: leave uninstalled (rejected; templates have
    existed for sessions; install closes the queue cleanly)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -8
echo

echo "Pushing B442..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B442 pushed. Session arc closed."
echo
echo "Press any key to close."
read -n 1 || true
