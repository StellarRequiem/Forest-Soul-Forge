#!/bin/bash
# Burst 419 - skill prompts learn about known chain anomalies + future_skill.
#
# B417 + B418 documented the chain anomalies and future_skill marker.
# But the LLM that runs wiring_audit.v1 + wiring_audit_triage.v1
# doesn't know about these — it sees raw chain_ok=False every cycle
# and reasons about it as a HIGH-severity finding, every time.
#
# Fix: bake the known-anomalies context into the skill prompts so
# the LLM correctly classifies them as INFO not HIGH. Per ADR-0081
# the triune's job is operator-prioritized output; if the prompt
# tells the LLM about documented anomalies, the output gets cleaner
# every run.
#
# What this commit adds:
#
# 1. examples/skills/wiring_audit.v1.yaml
#    summarize step prompt gets a "NOTE on chain_ok=False" block
#    that names the known fork seqs (3728, 3735-3738, 3740,
#    7695-7703) + audit-doc pointers. Severity mapping updated:
#    chain break at UNKNOWN seq is HIGH; chain break at documented
#    historical fork is INFO. future_skill declarations are INFO
#    not MEDIUM.
#
# 2. examples/skills/wiring_audit_triage.v1.yaml
#    reviewer_rank delegate prompt gets the same KNOWN HISTORICAL
#    CONTEXT block so Reviewer-Main's severity ranking step matches
#    the sentinel's own severity scale.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: triune triage reasons about chain_ok=False every
#     run, inflating output severity. Operator-actionable signal
#     drowns in known-anomaly noise.
#   Prove non-load-bearing: skill-manifest prompt text only.
#     No tool changes, no substrate changes. LLM learns context
#     from the prompt; classification gets cleaner.
#   Prove alternative: extend audit_chain_verify.v1 to accept a
#     known_forks parameter — substrate change, requires test
#     coverage, larger blast radius. Prompt update achieves the
#     same operator-visible outcome with skill-manifest changes
#     only.
#
# Operator sync (gitignored installed/ dir):
#   cp examples/skills/wiring_audit.v1.yaml data/forge/skills/installed/
#   cp examples/skills/wiring_audit_triage.v1.yaml data/forge/skills/installed/
#
# Verification after this commit lands:
#   1. bash dev-tools/run-wiring-audit.command
#      Expected: summary text no longer flags chain_ok=False as
#      HIGH severity; instead notes the documented historical fork.
#   2. bash dev-tools/run-triune-triage.command
#      Expected: triage prioritization treats chain break as INFO;
#      operator-burst-count estimate drops because the "fix the
#      chain" item is removed from the must-fix list.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/wiring_audit.v1.yaml \
        examples/skills/wiring_audit_triage.v1.yaml \
        dev-tools/commit-bursts/commit-burst419-skill-prompts-known-anomalies.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(skill): teach triune prompts about known chain anomalies (B419)

Burst 419. B417+B418 documented the chain anomalies + future_skill
marker, but the LLM running wiring_audit + wiring_audit_triage
didn't know about them. Every cycle reasoned about chain_ok=False
as HIGH severity even though both write-race episodes are
documented + accepted.

Bake the context into the prompts:

wiring_audit.v1 summarize: adds 'NOTE on chain_ok=False' block
  naming known fork seqs (3728, 3735-3738, 3740, 7695-7703) +
  audit-doc pointers. Severity mapping: chain break at UNKNOWN
  seq is HIGH; chain break at documented historical fork is INFO.
  future_skill declarations are INFO not MEDIUM.

wiring_audit_triage.v1 reviewer_rank: same KNOWN HISTORICAL
  CONTEXT block so Reviewer-Main's severity ranking matches the
  sentinel's scale.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: triune reasons about chain_ok=False every run;
    operator signal drowns in known-anomaly noise.
  Prove non-load-bearing: prompt text only; no substrate change.
  Prove alternative: extending audit_chain_verify to accept
    known_forks needs substrate code + test coverage; prompt
    update achieves same outcome with skill-manifest changes.

After landing: triune's burst-count estimates drop because
'fix the chain' is removed from must-fix list."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 419 complete - skill prompts updated ==="
echo "=========================================================="
echo "Press any key to close."
read -n 1 || true
