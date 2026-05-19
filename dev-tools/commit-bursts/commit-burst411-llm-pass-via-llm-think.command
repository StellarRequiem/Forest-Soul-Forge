#!/bin/bash
# Burst 411 - llm_pass.v1 switches base tool to llm_think.v1.
#
# B410's provider wiring fix unblocked Reviewer-Main's delegated
# llm_pass call. Reviewer-Main ran fine (real LLM output, severity-
# ranked items). But Architect-Main's call refused with
# 'tool_refused' because system_architect's standard_tools kit does
# NOT include text_summarize.v1 — only llm_think.v1 + memory + delegate
# + code_read.
#
# Two ways to fix:
#   (a) Add text_summarize.v1 to system_architect's kit in
#       tool_catalog.yaml. Requires re-birth of Architect-Main per
#       the constitution-immutability invariant (CLAUDE.md sec0).
#   (b) Switch llm_pass.v1 to wrap llm_think.v1 instead of
#       text_summarize.v1. llm_think is in every triune-relevant
#       kit (per B392 + role-by-role): software_engineer,
#       code_reviewer, system_architect, experimenter, every
#       observer/investigator/communicator/guardian/researcher
#       genre default.
#
# (b) is the right move:
#   - Lighter blast radius: one skill manifest swap instead of a
#     kit addition + rebirth cycle.
#   - Output schema is identical between the two tools ({response,
#     model, task_kind, elapsed_ms}), so callers reading
#     ${pass.response} work unchanged.
#   - llm_think's default system prompt derives from agent
#     role + genre — better signal for triune persona-framed
#     prompts than text_summarize's "compress to N words"
#     system prompt would be.
#   - text_summarize is a wrapper around llm_think specialized for
#     summarization. The triune doesn't need that specialization;
#     it needs general LLM passes with persona framing.
#
# What this commit adds:
#
# 1. examples/skills/llm_pass.v1.yaml
#    - requires: text_summarize.v1 -> llm_think.v1.
#    - inputs: text -> prompt; style + target_words removed;
#      max_tokens + task_kind added (matches llm_think input_schema).
#    - steps: tool text_summarize.v1 -> llm_think.v1; args adjusted.
#    - Output block unchanged (identical schema).
#    - Docstring records the rationale.
#
# 2. examples/skills/wiring_audit_triage.v1.yaml
#    - reviewer_rank + architect_priority delegate inputs:
#      style/target_words/text -> max_tokens/task_kind/prompt.
#      Each step picks a reasonable max_tokens (reviewer: 1200,
#      architect: 1500).
#
# Operator sync (gitignored installed/ dir):
#   cp examples/skills/llm_pass.v1.yaml \
#      data/forge/skills/installed/
#   cp examples/skills/wiring_audit_triage.v1.yaml \
#      data/forge/skills/installed/
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: Architect-Main refused step 3; triage incomplete.
#     Adding text_summarize to system_architect kit would work but
#     requires rebirth.
#   Prove non-load-bearing: one tool swap in llm_pass + arg-name
#     swap in two triage steps. Identical output schema means
#     callers untouched.
#   Prove alternative: option (a) kit add + rebirth is heavier;
#     llm_think is the correct base tool for general-purpose
#     persona-framed passes; specialization (text_summarize) was
#     incidental.
#
# Verification after this commit lands:
#   1. (already-synced installed/ dir from sandbox prior to commit)
#   2. bash dev-tools/run-triune-triage.command
#      Expected: all 4 steps succeed. reviewer_invoked_seq +
#      architect_invoked_seq populated. Memory entry attribution
#      includes all three agents.
#
# Hotfix cascade for ADR-0081 Option A:
#   B405 initial scaffolding
#   B406 bash-3.2 compat
#   B407 registry-direct memory read
#   B408 collapse delegate to single-agent
#   B409 llm_pass.v1 + restore real delegate
#   B410 delegate provider_resolver wiring fix
#   B411 (this) llm_pass via llm_think (not text_summarize)

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/llm_pass.v1.yaml \
        examples/skills/wiring_audit_triage.v1.yaml \
        dev-tools/commit-bursts/commit-burst411-llm-pass-via-llm-think.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(skill): llm_pass via llm_think instead of text_summarize (B411)

Burst 411. B410's provider fix unblocked Reviewer-Main. Reviewer
succeeded with real LLM output. Architect-Main refused with
tool_refused because system_architect's kit does NOT include
text_summarize.v1 — only llm_think.v1 + memory + delegate +
code_read.

Switch llm_pass.v1 to wrap llm_think.v1 instead. llm_think is in
every triune-relevant kit (B392 + role-by-role per tool_catalog).
Identical output schema ({response, model, task_kind, elapsed_ms})
so callers reading \${pass.response} work unchanged.

Better fit for triune persona prompts anyway: llm_think's default
system prompt derives from role+genre. text_summarize's system
prompt is 'compress to N words' which doesn't match what the
reviewer/architect personas want to do.

Avoids the alternative (add text_summarize to system_architect's
kit + rebirth Architect-Main). Lighter blast radius.

Changes:
  llm_pass.v1.yaml:
    - requires: text_summarize.v1 -> llm_think.v1
    - inputs: text/style/target_words -> prompt/max_tokens/task_kind
    - step args adjusted; output unchanged
  wiring_audit_triage.v1.yaml:
    - reviewer_rank + architect_priority delegate inputs renamed
    - max_tokens picked per step (reviewer 1200, architect 1500)

Hotfix cascade for Option A:
  B405-B408 initial + collapse
  B409 llm_pass.v1 + real delegate
  B410 delegate provider_resolver wiring fix
  B411 (this) llm_pass via llm_think

After landing: bash dev-tools/run-triune-triage.command should
reach status=succeeded with reviewer + architect invoked_seq
populated."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 411 complete - llm_pass via llm_think ==="
echo "=========================================================="
echo "Next: bash dev-tools/run-triune-triage.command"
echo ""
echo "Press any key to close."
read -n 1 || true
