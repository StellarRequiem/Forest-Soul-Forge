#!/bin/bash
# Burst 409 - llm_pass.v1 + migrate wiring_audit_triage to real delegate.
#
# Combined commit because the two changes are paired: llm_pass exists
# specifically as the delegate target for the triage skill (and future
# triune skills). Landing them separately would leave one half
# half-wired between commits.
#
# What this commit adds:
#
# 1. examples/skills/llm_pass.v1.yaml (NEW)
#    Generic single-step skill wrapping text_summarize.v1. Inputs
#    pass through verbatim (text + style + target_words); output
#    surfaces the wrapped tool's response/model/task_kind/elapsed_ms.
#    Single step, no memory writes, no side effects. Exists so
#    delegate.v1 has a real skill target on sister agents — delegate
#    invokes SKILLS, not tools, and we don't want every triune
#    participant to need a bespoke skill manifest per task.
#
# 2. examples/skills/wiring_audit_triage.v1.yaml
#    Restore real triune delegation: reviewer_rank + architect_priority
#    steps now use delegate.v1 with skill_name=llm_pass, targeting
#    Reviewer-Main + Architect-Main respectively. allow_out_of_lineage
#    flag set true (sister births, not bonded triune yet). delegate
#    return shape is {status, output, ...}; downstream interpolation
#    fixed to read \${step.output.response} not \${step.response}.
#    record + output blocks updated accordingly.
#
#    requires: gains delegate.v1.
#
# Why the previous collapse-to-single-agent (B408) wasn't permanent:
#   - Logical-only triune means audit chain attribution lies:
#     skill_invoked events land on Engineer-Main's chain even when
#     the prompt asks "you are the reviewer". The honest attribution
#     was always going to require real delegation.
#   - Other scheduled triune tasks (B411 commit_changelog, B412
#     code_review_quick) want the same pattern. Build llm_pass once,
#     reuse everywhere.
#
# Operator sync (gitignored installed/ dir, both files):
#   cp examples/skills/llm_pass.v1.yaml data/forge/skills/installed/
#   cp examples/skills/wiring_audit_triage.v1.yaml data/forge/skills/installed/
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping: every future triune skill that uses
#     delegate needs its own custom delegate-target skill on each
#     sibling, OR collapses to single-agent (B408 pattern). Doesn't
#     scale. Single-agent also lies on attribution.
#   Prove non-load-bearing: ADDITIONS only. llm_pass is brand new;
#     wiring_audit_triage's external behavior is unchanged
#     (3-perspective synthesis), only the dispatch shape is upgraded.
#   Prove alternative: build N bespoke delegate-target skills - won't
#     scale. Stay on single-agent forever - dishonest attribution.
#     llm_pass is the right shape.
#
# Verification after this commit lands:
#   1. (already-synced installed/ dir from sandbox prior to commit)
#   2. bash dev-tools/run-triune-triage.command
#      Expected: status=succeeded; reviewer_invoked_seq + architect_
#      invoked_seq populated; audit chain shows skill_invoked events
#      attributed to code_reviewer + system_architect (not just
#      software_engineer).
#   3. Verify chain attribution:
#        curl /audit/tail | grep -B2 -A2 'wiring_audit_triage\|llm_pass'
#      Expected: skill_invoked events for llm_pass on Reviewer-Main
#      and Architect-Main instance_ids.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/llm_pass.v1.yaml \
        examples/skills/wiring_audit_triage.v1.yaml \
        dev-tools/commit-bursts/commit-burst409-llm-pass-and-triage-delegate.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(skill): llm_pass.v1 + triage real-delegate upgrade (B409)

Burst 409. Two paired changes; landed together because llm_pass
exists specifically as the delegate target for the triage skill
(and future triune skills).

examples/skills/llm_pass.v1.yaml (NEW):
  Generic single-step skill wrapping text_summarize.v1. Inputs
  pass through (text + style + target_words); output surfaces
  the wrapped tool's response/model/task_kind/elapsed_ms. Exists
  so delegate.v1 has a real skill target on sister agents —
  delegate invokes SKILLS not tools.

examples/skills/wiring_audit_triage.v1.yaml:
  Restore real triune delegation. reviewer_rank +
  architect_priority steps use delegate.v1 with
  skill_name=llm_pass, targeting Reviewer-Main + Architect-Main.
  allow_out_of_lineage=true (sister births, not bonded yet).
  delegate returns {status, output, ...}; downstream interpolation
  updated to \${step.output.response}. Record + output blocks
  fixed. requires gains delegate.v1.

Why B408's collapse-to-single-agent wasn't permanent:
  - Audit chain attribution lies when reviewer + architect personas
    fire on Engineer-Main's chain.
  - Future scheduled triune tasks (B411 commit_changelog, B412
    code_review_quick) need the same delegate pattern. Build
    llm_pass once, reuse.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: future triune tasks need their own bespoke delegate-
    target skills OR collapse to single-agent. Doesn't scale.
  Prove non-load-bearing: ADDITION + refactor with preserved
    external behavior.
  Prove alternative: N bespoke skills = doesn't scale; stay
    single-agent = dishonest attribution. llm_pass is right shape.

Operator sync (gitignored):
  cp examples/skills/llm_pass.v1.yaml data/forge/skills/installed/
  cp examples/skills/wiring_audit_triage.v1.yaml data/forge/skills/installed/

After landing: bash dev-tools/run-triune-triage.command should
reach status=succeeded with reviewer_invoked_seq + architect_
invoked_seq populated (real delegation occurred)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 409 complete - llm_pass + triage delegate ==="
echo "=========================================================="
echo "Next: bash dev-tools/run-triune-triage.command"
echo ""
echo "Press any key to close."
read -n 1 || true
