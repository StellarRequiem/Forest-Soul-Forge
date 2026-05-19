#!/bin/bash
# Burst 414 - flatten .out. refs in commit_changelog.v1 + code_review_quick.v1.
#
# B412's verify run of Engineer-Main's commit_changelog.v1 reached
# step `summarize_for_audience` then failed:
#   failure_reason: expression_error
#   failure_detail: arg resolution failed: key 'out' missing on dict
#
# The skill manifest referenced `${cm.out.response}` — but tool
# outputs are FLAT (no .out. wrapper). The correct interpolation is
# `${cm.response}`. Same class of bug as B404 (wiring_audit.v1
# referenced summarize.summary instead of summarize.response).
#
# Pre-emptive sweep found the same gap in code_review_quick.v1:
#   read_file.out.content   -> read_file.content
#   explain.out.response    -> explain.response
#   critic.out.response     -> critic.response
#
# Both skills have been in examples/skills/ but were never run
# live. Now that Options B + C scheduled them on real triune
# work, the bug surfaced.
#
# Class of gap: ADR-0081 wiring-coverage section-15 needs an
# additional check: validate every step's args + output refs
# resolve against the wrapped tool's actual output keys (not just
# `${step.something}` syntactic OK). This is the same enhancement
# B404 documented. Queueing it explicitly here.
#
# What this commit adds:
#
# 1. examples/skills/commit_changelog.v1.yaml
#    Three .out.response -> .response substitutions (input text +
#    output block).
#
# 2. examples/skills/code_review_quick.v1.yaml
#    Four .out.x -> .x substitutions:
#      read_file.out.content -> read_file.content
#      explain.out.response  -> explain.response  (x2)
#      critic.out.response   -> critic.response
#
# Operator sync (gitignored):
#   cp examples/skills/commit_changelog.v1.yaml data/forge/skills/installed/
#   cp examples/skills/code_review_quick.v1.yaml data/forge/skills/installed/
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: Engineer-Main + Reviewer-Main scheduled tasks
#     both fail on second step due to bad output ref. Triune
#     B + C dead-on-arrival until fixed.
#   Prove non-load-bearing: seven YAML text substitutions across
#     two skill manifests. No new fields, no schema changes.
#   Prove alternative: leaving the .out. references in place means
#     the skills only work via fixture-mocked tests that mock
#     a .out wrapper. Live dispatch always fails.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/commit_changelog.v1.yaml \
        examples/skills/code_review_quick.v1.yaml \
        dev-tools/commit-bursts/commit-burst414-skill-flat-output-refs.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(skill): flatten .out. output refs in changelog + review (B414)

Burst 414. B412's verify of commit_changelog.v1 reached step
summarize_for_audience then failed:
  failure_reason: expression_error
  failure_detail: arg resolution failed: key 'out' missing on dict

Manifest referenced \${cm.out.response} but tool outputs are flat
(no .out. wrapper). Pre-emptive sweep found the same gap in
code_review_quick.v1. Both skills were in examples/skills/ but
never run live until Options B + C scheduled them.

Same class as B404 (wiring_audit.v1's summarize.summary vs
.response). Queued substrate enhancement: section-15 should
validate every step's args + output refs against wrapped tool's
output keys.

commit_changelog.v1: 3 substitutions
  \${cm.out.response} -> \${cm.response} (input text + output)
  \${summarize_for_audience.out.response} -> .response (output)

code_review_quick.v1: 4 substitutions
  read_file.out.content -> .content
  explain.out.response  -> .response (x2)
  critic.out.response   -> .response

Operator sync (gitignored):
  cp examples/skills/commit_changelog.v1.yaml data/forge/skills/installed/
  cp examples/skills/code_review_quick.v1.yaml data/forge/skills/installed/

After landing: both scheduled tasks should reach status=succeeded
on live verify."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 414 complete - skill flat output refs ==="
echo "=========================================================="
echo "Press any key to close."
read -n 1 || true
