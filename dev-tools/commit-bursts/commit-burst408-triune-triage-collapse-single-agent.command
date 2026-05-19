#!/bin/bash
# Burst 408 - wiring_audit_triage collapse to single-agent execution.
#
# Triage step 2 failed at run-time with:
#   failure_reason: tool_failed
#   failure_detail: tool delegate.v1 raised ToolValidationError
#
# Root cause: my manifest passed `skill_name: text_summarize` to
# delegate.v1. But delegate invokes SKILLS on the target, not tools.
# text_summarize is a tool, not a skill. delegate's validator failed
# because the named skill doesn't exist in data/forge/skills/installed/
# (there's no text_summarize.v1.yaml — only the wrapping skills that
# call the tool).
#
# Two paths forward:
#   (a) Build a generic single-step wrapper skill (llm_pass.v1) that
#       delegate.v1 can target. Then text_summarize calls go through
#       the skill engine on the target agent. Architecturally clean
#       but adds a new skill that needs install + verify.
#   (b) Collapse to single-agent execution: Engineer-Main runs all
#       three text_summarize calls in sequence with explicit persona
#       prompts ("You are the code-reviewer", "You are the system-
#       architect"). The triune is logical/persona-based, not bound
#       to three separate dispatches. Real LLM output, real multi-
#       perspective synthesis.
#
# Chose (b) for this first scheduled triune task — keeps the commit
# small, gets useful output today, defers the llm_pass.v1 generic
# wrapper to when other triune tasks need it.
#
# Architectural attribution preserved: the wrapper still resolves
# Reviewer-Main + Architect-Main instance_ids and passes them as
# inputs; the final memory_write records them as attribution so the
# memo can be re-attributed when the delegate path lands later.
#
# What this commit adds:
#
# 1. examples/skills/wiring_audit_triage.v1.yaml
#    - reviewer_rank: tool delegate.v1 -> tool text_summarize.v1
#    - architect_priority: tool delegate.v1 -> tool text_summarize.v1
#    - requires: drop delegate.v1 (no longer used)
#    - Comment block explaining the choice + the future llm_pass.v1
#      upgrade path.
#
# Operator sync step:
#   cp examples/skills/wiring_audit_triage.v1.yaml \
#      data/forge/skills/installed/wiring_audit_triage.v1.yaml
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: status=failed at reviewer_rank; triage cannot complete.
#   Prove non-load-bearing: two step tool fields changed, requires
#     trimmed. Skill semantics preserved (three persona-prompted LLM
#     passes); only the dispatch shape differs.
#   Prove alternative: (a) llm_pass.v1 — better long-term, more work
#     now. (b) single-agent — works today, deferred upgrade is cheap
#     once llm_pass.v1 exists.
#
# Hotfix cascade tally for ADR-0081 Option A:
#   B405 — initial Triune-Main scheduled triage scaffolding.
#   B406 — bash-3.2 compat (macOS /bin/bash).
#   B407 — registry-direct memory read (no /agents/{id}/memory GET).
#   B408 — collapse delegate to single-agent text_summarize.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/wiring_audit_triage.v1.yaml \
        dev-tools/commit-bursts/commit-burst408-triune-triage-collapse-single-agent.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(triune): collapse triage to single-agent text_summarize (B408)

Burst 408. wiring_audit_triage step 2 failed at run-time:
  failure_reason: tool_failed
  failure_detail: tool delegate.v1 raised ToolValidationError

Root cause: manifest passed skill_name=text_summarize to
delegate.v1. delegate invokes SKILLS on the target, not tools.
text_summarize is a tool; no text_summarize.v1.yaml skill exists
in installed/. delegate's validator rejected.

Fix: collapse to single-agent execution. Engineer-Main runs all
three text_summarize calls in sequence with explicit persona
prompts. Reviewer-Main + Architect-Main instance_ids stay as
inputs for attribution recording.

Future upgrade path: build a generic llm_pass.v1 single-step
wrapper skill that delegate.v1 can target. Migrate this skill +
any future triune tasks to the delegate path once llm_pass.v1
ships. Documented in skill manifest comment.

Hotfix cascade for Option A:
  B405 initial scaffolding
  B406 bash-3.2 compat
  B407 registry-direct memory read
  B408 (this) collapse delegate to single-agent

Operator sync (gitignored): cp examples/skills/wiring_audit_triage.v1.yaml
  data/forge/skills/installed/

After landing: bash dev-tools/run-triune-triage.command should
reach status=succeeded."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 408 complete - triage collapse to single agent ==="
echo "=========================================================="
echo "Next: bash dev-tools/run-triune-triage.command"
echo ""
echo "Press any key to close."
read -n 1 || true
