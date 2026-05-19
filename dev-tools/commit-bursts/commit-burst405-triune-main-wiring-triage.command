#!/bin/bash
# Burst 405 - Triune-Main scheduled wiring triage (Option A).
#
# First scheduled autonomous-work cell. Three named agents
# (Engineer-Main / Reviewer-Main / Architect-Main) run a daily
# triage of the most-recent WiringSentinel outcome. Output is a
# prioritized remediation order memo persisted to Engineer-Main's
# lineage memory + audit chain.
#
# Why this first scheduled triune task:
#   - Uses REAL data (the wiring_audit_outcome the just-verified
#     sentinel produces every 4 hours), not synthetic test fixtures.
#   - Zero side effects: all three roles run read_only or read_only-
#     equivalent skill steps. text_summarize produces text; the
#     final memory_write goes to Engineer-Main's own lineage only.
#   - Operator-visible value: the triune turns the sentinel's flat
#     gap dump into a severity-ranked + dependency-aware execution
#     order. If the LLMs produce useful signal, the operator can
#     work through the queue burst-by-burst.
#   - Tests the triune flow end-to-end. If wiring_audit_triage works,
#     the same pattern (engineer→reviewer→architect via delegate)
#     extends to other triune tasks (commit_changelog, release_notes,
#     code_review_quick on real diffs).
#
# What this commit adds:
#
# 1. dev-tools/birth-triune-main.command (NEW)
#    3-phase birth driver. Idempotent. Births Engineer-Main
#    (software_engineer, actuator genre, posture YELLOW because
#    kit includes shell_exec + code_edit which need operator
#    allowlists), Reviewer-Main (code_reviewer, guardian, GREEN —
#    read_only kit), Architect-Main (system_architect, researcher,
#    GREEN — read_only kit). Posture YELLOW on Engineer-Main is
#    the load-bearing safety: scheduled work won't actually exercise
#    code_edit or shell_exec until operator-driven allowlists land.
#    The scheduled triage skill itself uses only text_summarize +
#    delegate + memory_write — all GREEN-safe.
#
# 2. examples/skills/wiring_audit_triage.v1.yaml (NEW)
#    4-step skill: engineer_extract (text_summarize on raw outcome)
#    -> reviewer_rank (delegate to Reviewer-Main with severity
#    mapping prompt) -> architect_priority (delegate to Architect-
#    Main with dependency-reasoning prompt) -> record (memory_write
#    full triage outcome to Engineer-Main's lineage). Delegates use
#    allow_out_of_lineage=true because Triune-Main agents are sister
#    births (not bonded triune); audit chain records the cross-
#    lineage override for attribution.
#
# 3. dev-tools/run-triune-triage.command (NEW)
#    Scheduled wrapper. Self-heals the gitignored installed/ skill
#    manifest, resolves all four instance_ids (Engineer-Main +
#    Reviewer-Main + Architect-Main + WiringSentinel), reads the
#    most-recent wiring_audit_outcome from WiringSentinel's lineage,
#    dispatches wiring_audit_triage.v1 on Engineer-Main with the
#    outcome + sibling instance_ids as inputs. Soft-fails on any
#    upstream gap; launchd retries next tick.
#
# 4. dev-tools/launchd/dev.forest.triune-triage.plist.template (NEW)
#    LaunchAgent template. StartCalendarInterval 7:00 daily (not
#    StartInterval — operator wants predictable morning brief, not
#    sliding 24h offset). Logs to /tmp/forest-triune-triage.*.log.
#    Install/uninstall by hand; opt-in operator step.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping: WiringSentinel produces gaps every 4h
#     but they accumulate as undifferentiated memory entries. No
#     prioritization, no dependency reasoning. Operator has to
#     read each outcome by hand. Doesn't scale.
#   Prove non-load-bearing: ADDITIONS only. Three new births
#     (no existing role/agent affected). New skill manifest. New
#     wrapper script. New plist template (install is opt-in
#     operator step). No code changes to substrate.
#   Prove alternative is strictly better:
#     (a) operator reads wiring_audit memory entries by hand —
#         what we have today; doesn't scale + no severity ranking.
#     (b) extend the wiring_audit.v1 skill to also do triage —
#         conflates the sentinel's 'find' role with the triune's
#         'prioritize' role; violates ADR-0081 D5 (sentinel finds,
#         operator-equivalent triune triages).
#     (c) build a single 'super-agent' that does both — same
#         conflation + no multi-perspective review.
#
# Verification after this commit lands:
#   1. force-restart-daemon (no substrate change required; the new
#      skill auto-loads from installed/ on first dispatch).
#   2. bash dev-tools/birth-triune-main.command
#      Expected: three births, postures set (Engineer-Main yellow,
#      Reviewer-Main + Architect-Main green).
#   3. bash dev-tools/run-triune-triage.command
#      Expected: section-15 + wiring_audit already produced an
#      outcome (today's run). Triage dispatches the 4-step skill;
#      status=succeeded; output non-null. Engineer-Main lineage
#      memory gains a triune_main_wiring_triage entry.
#   4. (optional) Install the plist for 7am daily cadence.
#
# This is the FIRST scheduled triune task. If it works, future
# scheduled tasks reuse the same pattern: build the skill manifest,
# write a wrapper that resolves instance_ids + injects inputs,
# install a plist with a sensible cadence, verify manually before
# enabling the launchd schedule.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/birth-triune-main.command \
        examples/skills/wiring_audit_triage.v1.yaml \
        dev-tools/run-triune-triage.command \
        dev-tools/launchd/dev.forest.triune-triage.plist.template \
        dev-tools/commit-bursts/commit-burst405-triune-main-wiring-triage.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(triune): Triune-Main scheduled wiring triage (B405)

Burst 405. First scheduled autonomous-work cell. Three named
agents (Engineer-Main + Reviewer-Main + Architect-Main) run a
daily triage of WiringSentinel's most-recent wiring_audit_outcome.
Output is a prioritized remediation order memo persisted to
Engineer-Main's lineage memory + audit chain.

dev-tools/birth-triune-main.command:
  3-phase birth driver. Engineer-Main (software_engineer,
  actuator, posture YELLOW because kit includes shell_exec +
  code_edit), Reviewer-Main (code_reviewer, guardian, GREEN
  read_only), Architect-Main (system_architect, researcher,
  GREEN read_only). Posture YELLOW on Engineer is load-bearing;
  scheduled triage doesn't exercise the gated tools (uses only
  text_summarize + delegate + memory_write).

examples/skills/wiring_audit_triage.v1.yaml:
  4-step skill:
    engineer_extract  - text_summarize on raw outcome
    reviewer_rank     - delegate to Reviewer-Main, severity per
                        ADR-0081 §severity
    architect_priority - delegate to Architect-Main, dependency-
                         aware execution order
    record            - memory_write triage outcome to lineage

  allow_out_of_lineage=true on delegates (sister births, not
  bonded triune yet; audit chain records cross-lineage overrides
  for attribution).

dev-tools/run-triune-triage.command:
  Scheduled wrapper. Self-heals installed/ skill manifest,
  resolves all four instance_ids, fetches latest
  wiring_audit_outcome from WiringSentinel lineage, dispatches
  wiring_audit_triage.v1 with sibling ids + outcome as inputs.

dev-tools/launchd/dev.forest.triune-triage.plist.template:
  StartCalendarInterval 7:00 daily (predictable morning brief).
  Install by hand; opt-in operator step.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm of NOT shipping: sentinel produces gaps every 4h
    but they accumulate undifferentiated. No prioritization, no
    dependency reasoning. Operator reads by hand. Doesn't scale.
  Prove non-load-bearing: ADDITIONS only. No substrate changes.
  Prove alternative is better: extending wiring_audit.v1 to also
    do triage conflates sentinel's 'find' with triune's
    'prioritize' (ADR-0081 D5 says sentinel finds, operator-
    equivalent triages).

After landing:
  1. force-restart-daemon
  2. bash dev-tools/birth-triune-main.command
  3. bash dev-tools/run-triune-triage.command
  4. (optional) Install plist for 7am cadence."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 405 complete - Triune-Main first scheduled task ==="
echo "=========================================================="
echo "Next:"
echo "  1. force-restart-daemon"
echo "  2. bash dev-tools/birth-triune-main.command"
echo "  3. bash dev-tools/run-triune-triage.command"
echo ""
echo "Press any key to close."
read -n 1 || true
