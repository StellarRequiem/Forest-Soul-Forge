#!/bin/bash
# Burst 412 - Options B + C scheduled triune tasks.
#
# Option B (daily commit_changelog): Engineer-Main reads last 24h
# of git log, dispatches commit_changelog.v1, produces a 3-bullet
# user-facing changelog in lineage memory. Daily 6am cadence.
#
# Option C (weekly code_review_quick): Reviewer-Main picks the
# most-recent production .py file changed in last 7 days,
# dispatches code_review_quick.v1, produces a critic-style review
# in lineage memory. Weekly Monday 8am cadence.
#
# Both are SINGLE-AGENT execution (not triune delegate chain).
# B's commit_message + text_summarize and C's code_read +
# code_explain + llm_think all live in the single dispatching
# agent's kit. Multi-agent delegation isn't needed; single-skill
# dispatch is the right shape.
#
# Combined commit because both share the same wrapper + plist
# template + skill-install pattern. Each is independently useful;
# operator can install one or both plists.
#
# What this commit adds:
#
# 1. examples/skills/commit_changelog.v1.yaml + code_review_quick.v1.yaml
#    Already exist as source-of-truth manifests. NOT modified.
#    Copied to data/forge/skills/installed/ via the per-wrapper
#    self-heal step (gitignored installed/ dir).
#
# 2. dev-tools/run-engineer-changelog.command (NEW)
#    3-step driver: self-heal skill copy, resolve Engineer-Main,
#    build last-24h git log + shortstat as diff input, dispatch
#    commit_changelog.v1. audience="operators".
#
# 3. dev-tools/run-reviewer-review.command (NEW)
#    3-step driver: self-heal skill copy, resolve Reviewer-Main,
#    pick most-recent production .py file changed in last 7 days
#    (skipping tests/__pycache__/.venv/.bak), dispatch
#    code_review_quick.v1 with language=python.
#
# 4. dev-tools/launchd/dev.forest.engineer-changelog.plist.template
#    Daily 6am. RunAtLoad=false.
#
# 5. dev-tools/launchd/dev.forest.reviewer-review.plist.template
#    Weekly Monday 8am. RunAtLoad=false. Note: 8am picks an hour
#    after Engineer's 6am so the two don't race; review reads
#    against fresh weekly state without competing for the
#    write-lock.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping: scheduled triune verified yesterday
#     (B405-B411) covers triage-only. Options B + C cover the other
#     two recurring needs Alex flagged (daily op-brief + weekly
#     code review). Without these, the daemon's named triune sits
#     idle 23 hours a day.
#   Prove non-load-bearing: ADDITIONS only. Two wrapper scripts +
#     two plist templates + copies of existing skill manifests.
#     Existing skills (commit_changelog.v1 + code_review_quick.v1)
#     are unchanged. Both plists are install-by-hand operator opt-in.
#   Prove alternative: real delegate triune for both - overreach;
#     single-skill dispatch is the right shape. Skip B + C entirely -
#     wastes the named triune outside the 4-hour wiring audit cycle.
#
# Verification after this commit lands:
#   1. bash dev-tools/run-engineer-changelog.command
#      Expected: status=succeeded; Engineer-Main lineage memory
#      gains an entry tagged 'commit_changelog' (per the skill's
#      own memory_write step if any, else just the chain entry).
#   2. bash dev-tools/run-reviewer-review.command
#      Expected: status=succeeded; Reviewer-Main reads + explains +
#      critiques one Python file; lineage memory captures the
#      review.
#   3. Install plists by hand per the install comments at top of
#      each template.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/run-engineer-changelog.command \
        dev-tools/run-reviewer-review.command \
        dev-tools/launchd/dev.forest.engineer-changelog.plist.template \
        dev-tools/launchd/dev.forest.reviewer-review.plist.template \
        dev-tools/commit-bursts/commit-burst412-options-b-c-scheduled-tasks.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(triune): Options B + C scheduled tasks (B412)

Burst 412. Two scheduled single-agent tasks for the Triune-Main
work cell.

Option B (daily): Engineer-Main runs commit_changelog.v1 against
last 24h of git log. 6am daily cadence. Produces operator-audience
3-bullet changelog in lineage memory.

Option C (weekly): Reviewer-Main runs code_review_quick.v1 against
most-recent production .py file changed in last 7 days. Monday 8am
cadence. Produces critic-style review in lineage memory.

Both single-agent (not triune delegate chain) — single-skill
dispatch is the right shape; the triune triage (B405-B411) IS the
multi-agent showcase. B + C just need a single dispatching agent.

Adds:
  dev-tools/run-engineer-changelog.command
  dev-tools/run-reviewer-review.command
  dev-tools/launchd/dev.forest.engineer-changelog.plist.template
  dev-tools/launchd/dev.forest.reviewer-review.plist.template

Skill manifests (commit_changelog.v1 + code_review_quick.v1) are
unchanged; wrappers self-heal the gitignored installed/ copies.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: triune sits idle 23h/day without B + C; only triage
    runs (4h cadence).
  Prove non-load-bearing: additions only. Install opt-in.
  Prove alternative: full delegate triune for these = overreach;
    skip = wastes named triune.

After landing:
  bash dev-tools/run-engineer-changelog.command
  bash dev-tools/run-reviewer-review.command
  install plists per top-of-template comments."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 412 complete - Options B + C shipped ==="
echo "=========================================================="
echo "Verify:"
echo "  bash dev-tools/run-engineer-changelog.command"
echo "  bash dev-tools/run-reviewer-review.command"
echo ""
echo "Press any key to close."
read -n 1 || true
