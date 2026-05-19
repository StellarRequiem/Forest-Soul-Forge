#!/bin/bash
# Burst 402 - ADR-0081 HOTFIX-3: wiring_audit.v1 style arg.
#
# Surfaced on B401's re-verify: wiring_audit.v1 dispatched and ran
# steps verify_chain + recall_prior, then failed at step `summarize`
# with:
#
#   failure_reason: tool_refused
#   failure_detail: tool text_summarize.v1 refused: bad_args —
#     style must be one of ['bullet_points', 'paragraph', 'tldr'];
#     got 'punch_list'
#
# T4 (B397) authored the manifest with style=punch_list — semantic
# choice ('produce a severity-tagged punch list') but not a real
# enum value of the wrapped tool. text_summarize.v1's accepted enum
# is {bullet_points, paragraph, tldr}. bullet_points is closest to
# the prompt's intent.
#
# This is the same gap-class section-15 catches (skill requires
# resolve in catalog) ONE LEVEL DOWN — the manifest's tool args have
# to also match each tool's input schema, not just have the tool
# present in the kit. Future enhancement to section-15: validate
# every step's args against the tool's input_schema.
#
# What this commit adds:
#
# 1. examples/skills/wiring_audit.v1.yaml — style: punch_list ->
#    style: bullet_points. Comment notes the enum constraint so
#    future-me doesn't repeat the mistake.
#
# Also (out-of-commit): the operator must sync the change to
# data/forge/skills/installed/wiring_audit.v1.yaml — the runtime
# install location is gitignored per-host state. The
# run-wiring-audit.command wrapper's self-heal step only copies on
# FIRST run; subsequent updates need a manual cp OR a re-install
# step. For this hotfix, the operator should run:
#
#   cp examples/skills/wiring_audit.v1.yaml \
#      data/forge/skills/installed/wiring_audit.v1.yaml
#
# B401 added the self-heal; future work: extend self-heal to detect
# stale install (mtime diff) and re-copy automatically.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: wiring_audit.v1 dispatch reaches summarize step then
#     refuses with bad_args. Status = failed. Skill cannot complete.
#   Prove non-load-bearing: one enum value change in one manifest.
#   Prove alternative: punch_list is not an actual style enum;
#     bullet_points is the literal-correct value per text_summarize's
#     input_schema. No other option.
#
# Verification after this commit lands:
#   1. cp examples/skills/wiring_audit.v1.yaml data/forge/skills/installed/
#      (or rely on next B401 self-heal-on-mtime-diff once landed)
#   2. bash dev-tools/run-wiring-audit.command
#      Expected: status=succeeded. summary_text non-empty. Lineage
#      memory gains a wiring_audit_outcome entry.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/wiring_audit.v1.yaml \
        dev-tools/commit-bursts/commit-burst402-adr0081-hotfix-wiring-audit-style.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(skill): wiring_audit.v1 style arg (ADR-0081 HOTFIX-3, B402)

Burst 402. HOTFIX-3 for B397. Skill dispatched + ran two steps
then failed at summarize with:
  failure_reason: tool_refused
  failure_detail: bad_args - style must be one of
    ['bullet_points', 'paragraph', 'tldr']; got 'punch_list'

T4 (B397) wrote style=punch_list (semantic choice 'produce a
severity-tagged punch list') but it's not in text_summarize.v1's
accepted enum. bullet_points is closest to the prompt's intent.

Fix: examples/skills/wiring_audit.v1.yaml
  style: punch_list -> style: bullet_points
  Plus comment noting the enum so future-me doesn't repeat it.

Class of gap: same as section-15's 'skill requires resolve in
catalog' check but ONE LEVEL DOWN - args must match each tool's
input_schema, not just have the tool in the kit. Future
section-15 enhancement: validate every step's args against
input_schema.

Operator sync step (gitignored install dir):
  cp examples/skills/wiring_audit.v1.yaml data/forge/skills/installed/

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: dispatch reaches summarize then refuses. status=failed.
  Prove non-load-bearing: one enum change.
  Prove alternative: only valid enum value choices are the 3
    accepted; bullet_points is the right fit."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 402 complete - ADR-0081 HOTFIX-3 shipped ==="
echo "=========================================================="
echo "Next:"
echo "  (already synced installed dir from sandbox prior to commit)"
echo "  bash dev-tools/run-wiring-audit.command"
echo ""
echo "Press any key to close."
read -n 1 || true
