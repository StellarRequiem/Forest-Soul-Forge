#!/usr/bin/env bash
# Burst 441 — CLAUDE.md sec6 + reviewer-review launchd installer.
#
# Bundle:
#   * CLAUDE.md sec6 — Read the ADR before assuming what its MVP is.
#     B440 lesson: I proposed a 3-benchmark substrate-perf MVP for
#     "ADR-0023 Benchmark Suite," then reading the ADR revealed it
#     specs per-genre quality batteries (10 tranches, kernel work).
#     Codifies the discipline to read the full ADR body before
#     committing to an MVP shape; adjacent scopes ship as their own
#     thing, not mislabeled as the ADR's MVP.
#
#   * dev-tools/install-launchd-reviewer-review.command (new) —
#     idempotent installer for dev.forest.reviewer-review.plist.
#     Mirrors the B439 wiring-audit installer pattern. Mon 8am
#     cadence runs Reviewer-Main on code_review_quick.v1 against
#     the repo per Option C from the 2026-05-19 Triune Options
#     session.
#
# Session deltas this turn:
#   * CLAUDE.md sec6 written.
#   * Fresh diagnostic-all confirms 15/15 PASS against ba0f7f5.
#   * dev.forest.reviewer-review.plist bootstrapped to
#     ~/Library/LaunchAgents/. Installed launchd plists now: 4 of 6
#     (daemon + ollama + wiring-audit + reviewer-review; remaining:
#     engineer-changelog + triune-triage).
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: B440 nearly mislabeled its scope as 'ADR-0023 MVP'
#     when it was something different; without sec6 the next
#     long-running ADR risks the same misread.
#     Reviewer-Main scheduled cadence has been queued since the
#     2026-05-19 Triune Options session per Option C; install
#     closes that pending item without LLM-time cost outside the
#     Monday 8am window.
#   Prove non-load-bearing: docs sec-rule + dev-tools installer.
#     No schema, no events, no routes.
#   Prove alternative: skip the sec6 entry (rejected; the lesson
#     is concrete and the existing sec1-sec5 structure invites it).
#     Defer reviewer-review install (rejected; user opted in by
#     completing B428/B429 sibling-3 allowed_paths work).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 441 — CLAUDE.md sec6 + reviewer-review installer"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add CLAUDE.md
git add dev-tools/install-launchd-reviewer-review.command
git add dev-tools/commit-bursts/commit-burst441-claude-md-sec6-and-reviewer-review-install.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "chore(governance): CLAUDE.md sec6 (ADR-MVP-scope discipline) + reviewer-review launchd installer (B441)

Bundles two closely-related items from the post-B440 audit:

(1) CLAUDE.md sec6 — Read the ADR before assuming what its MVP is.
    Codifies the B440 lesson. ChatGPT critique flagged 'ADR-0023
    Benchmark Suite never shipped'; I proposed a 3-benchmark
    substrate-perf MVP. Reading the ADR body revealed it specs
    per-genre quality batteries with HTTP routes, audit event
    types, registry table, fixture YAML schema, rubric scoring —
    multi-burst kernel work, completely different scope. Mine
    was an adjacent substrate-perf measurement tool that
    complements ADR-0023, not its MVP.
    Rule: read the full ADR body before committing to an MVP
    shape. Distinguish 'this ADR's scope' from 'adjacent scope
    the ADR mentions.' If shipping something adjacent, ship it
    as its own thing with explicit scope-clarification docs.
    Same structure as sec1-sec5: rule + load-bearing example +
    how-to-apply.

(2) dev-tools/install-launchd-reviewer-review.command (NEW) —
    idempotent installer for dev.forest.reviewer-review.plist.
    Mirrors the B439 wiring-audit installer. Mon 8am cadence
    runs Reviewer-Main (sibling-3 code_reviewer_8808e39f43ac_3,
    posture green with allowed_paths from B428/B429) on
    code_review_quick.v1 against the repo. Closes Option C from
    the 2026-05-19 Triune Options session.

Session milestones this turn (not in commit, recorded for
provenance):
  * Fresh diagnostic-all reports 15/15 PASS against ba0f7f5.
  * dev.forest.reviewer-review.plist bootstrapped into user GUI
    launch domain. Installed plists now: 4 of 6 (daemon + ollama
    + wiring-audit + reviewer-review). Remaining 2:
    engineer-changelog, triune-triage.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: sec6 absent invites the next ADR-scope misread;
    reviewer-review cadence has been queued multiple sessions.
  Prove non-load-bearing for kernel: docs + dev-tools script.
    No schema, no events, no routes.
  Prove alternative: skip sec6 (rejected; concrete lesson, fits
    sec1-sec5 structure); defer reviewer-review install (rejected;
    sibling-3 allowed_paths work made install actionable)." || { echo "commit failed"; exit 1; }

echo
echo "Post-commit signature status:"
git log --format='%h %G? %s' -5
echo

echo "Pushing B441..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B441 pushed."
echo
echo "Press any key to close."
read -n 1 || true
