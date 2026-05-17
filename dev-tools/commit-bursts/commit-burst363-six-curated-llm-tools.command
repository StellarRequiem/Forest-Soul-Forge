#!/bin/bash
# Burst 363 - six curated-prompt LLM tools that revive the dead-
# skill set flagged by section-02-skill-manifests.
#
# D-1 path (a): wire the missing tools. Operator decision recorded
# in the iteration plan: 'tackle the substantive option, not the
# retire-skills shortcut.'
#
# What this lands:
#
#   src/forest_soul_forge/tools/builtin/_prompt_template_base.py
#     Shared base class. Each curated tool inherits validate()
#     boilerplate (max_tokens / task_kind / temperature clamping +
#     a text-field helper) and execute() boilerplate (provider
#     lookup, usage_cap clamp, elapsed_ms timing, token estimate,
#     ToolResult assembly). Subclasses override _validate_specific
#     and _build_prompts only. read_only side_effects across the
#     family - same posture as llm_think.v1; Guardian-genre agents
#     reach without per-call human approval.
#
#   src/forest_soul_forge/tools/builtin/text_summarize.py
#     Inputs: text (≤16k), target_words (10-1500), style
#     (bullet_points/paragraph/tldr), focus (optional). Revives
#     5 skills: agent_activity_digest, agent_introspect,
#     commit_changelog, memory_consolidate, morning_briefing
#     (release_notes references text_summarize too).
#
#   src/forest_soul_forge/tools/builtin/code_explain.py
#     Inputs: code, audience (novice/peer/expert), language hint,
#     focus. Revives 2 skills: bug_report_polish, code_review_quick.
#
#   src/forest_soul_forge/tools/builtin/email_draft.py
#     Inputs: intent, recipient, sender, tone (formal/friendly/
#     direct/apologetic/neutral), length (short/medium/long),
#     context. NEVER sends an email (no network side-effects);
#     output is text only for operator review. Revives 2 skills:
#     bug_report_polish, meeting_followup.
#
#   src/forest_soul_forge/tools/builtin/commit_message.py
#     Inputs: diff, format (conventional/imperative/plain), scope,
#     ticket. NEVER runs git; output is commit-message text only.
#     Revives 2 skills: commit_changelog, release_notes.
#
#   src/forest_soul_forge/tools/builtin/action_items_extract.py
#     Inputs: text, limit (1-50), require_owner (bool). Returns a
#     structured action-item list. Revives 1 skill: meeting_followup.
#
#   src/forest_soul_forge/tools/builtin/tone_shift.py
#     Inputs: text, target_tone (one of 11 registers),
#     preserve_structure. Preserves facts/numbers literally; only
#     surrounding prose is rewritten. Revives 1 skill: release_notes.
#
#   src/forest_soul_forge/tools/builtin/__init__.py
#     Imports + register_builtins wires the six new tools. Keep
#     CLAUDE.md sec3 discipline: _VERSION = "1" bare (not "v1"),
#     so registry keys compose to .v1 not .vv1. Section-04 will
#     verify catalog ↔ registered parity after restart.
#
#   config/tool_catalog.yaml
#     Six new entries with description, input_schema, side_effects,
#     sandbox_eligible=false (need ctx.provider), and per-role
#     archetype_tags. Total catalog grows 61 -> 67.
#
#   tests/unit/test_b363_curated_llm_tools.py
#     28 tests: validate() and _build_prompts() smoke for each
#     tool + 'all six in catalog' + 'all six register'. The
#     execute() path needs a live provider; diagnostic harness
#     section 07 (skill-smoke) exercises that surface on a live
#     daemon. Local run: 28 passed in 0.25s.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: 9 skills currently dead in section-02 because
#     they reference these 6 tools that aren't in the catalog.
#     The harness FAILs daily; the skills can't be invoked.
#   Prove non-load-bearing: additive only. No existing tool's
#     behavior changes. The shared base is internal; no external
#     API surface added beyond the 6 new tool keys.
#   Prove alternative is strictly better: D-1 path (b) was to
#     retire the 9 skills. That removes capability the skill names
#     imply we offer and loses the test-coverage they represent.
#     Path (a) is more work but recovers the surface; the iteration
#     plan presented options and the operator chose path (a).
#
# CLAUDE.md sec2 (B350 wiring discipline) check:
#   None of the six tools reach into ToolContext for a new typed
#   subsystem - they only need ctx.provider, ctx.constraints, and
#   ctx.role/genre which are already populated. So no dispatcher
#   wire is needed beyond the registry.register calls in __init__.py.
#
# CLAUDE.md sec3 (bare version strings):
#   All six classes declare version = "1" - bare numeric, not "v1".
#   Section-04 tool-registration verifies this on first run after
#   daemon restart.
#
# Verification after this commit lands:
#   1. force-restart-daemon.command - loads the 6 new tool classes.
#   2. dev-tools/diagnostic/section-04-tool-registration.command -
#      catalog=67 == registered=67+forged.
#   3. dev-tools/diagnostic/section-02-skill-manifests.command -
#      all 9 previously-dead skills now PASS (their requires-tool
#      lookups resolve).
#   4. diagnostic-all.command - should drop to 0 FAILs.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/builtin/_prompt_template_base.py \
        src/forest_soul_forge/tools/builtin/text_summarize.py \
        src/forest_soul_forge/tools/builtin/code_explain.py \
        src/forest_soul_forge/tools/builtin/email_draft.py \
        src/forest_soul_forge/tools/builtin/commit_message.py \
        src/forest_soul_forge/tools/builtin/action_items_extract.py \
        src/forest_soul_forge/tools/builtin/tone_shift.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_b363_curated_llm_tools.py \
        dev-tools/commit-bursts/commit-burst363-six-curated-llm-tools.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(tools): six curated LLM-wrapper tools (B363)

Burst 363. D-1 path (a) - wire the missing tools. Revives 9
skills section-02 flagged as dead.

Six tools, all read_only, all extending the shared
_prompt_template_base:

  text_summarize.v1   - 5 skills (agent_activity_digest,
                        agent_introspect, commit_changelog,
                        memory_consolidate, morning_briefing,
                        release_notes referenced too)
  code_explain.v1     - 2 skills (bug_report_polish,
                        code_review_quick)
  email_draft.v1      - 2 skills (bug_report_polish,
                        meeting_followup). NEVER sends; output
                        is text only.
  commit_message.v1   - 2 skills (commit_changelog,
                        release_notes). NEVER runs git.
  action_items_extract.v1 - 1 skill (meeting_followup)
  tone_shift.v1       - 1 skill (release_notes). Preserves
                        facts/numbers literally.

Shared base (_prompt_template_base.py):
  validate()  - max_tokens / task_kind / temperature clamping +
                _validate_text_field helper
  execute()   - provider lookup, usage_cap clamp, elapsed_ms,
                token estimate, ToolResult assembly
  Subclasses override _validate_specific + _build_prompts only.

CLAUDE.md sec2 check: none of the six need a new dispatcher
subsystem - ctx.provider + ctx.constraints + ctx.role/genre are
already populated, so no typed field / dispatcher wire / section-
06 probe needed beyond the registry.register calls.

CLAUDE.md sec3 check: all six declare version = '1' bare numeric,
not 'v1'. Section-04 verifies on first run.

Catalog: 61 -> 67 entries.
Tests: 28 passed in 0.25s.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 9 skills dead daily in section-02; can't be invoked.
  Prove non-load-bearing: additive; no existing behavior changes.
  Prove alternative is better: D-1 path (b) would retire the 9
    skills, losing capability the names imply we offer. Operator
    chose path (a).

After this lands + daemon restart:
  - section-02 should PASS (all 9 skills resolve).
  - section-04 should PASS (67 catalog == 67+forged registered).
  - diagnostic-all should drop to 0 FAIL."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 363 complete - six curated LLM tools ==="
echo "=========================================================="
echo "Restart daemon: dev-tools/force-restart-daemon.command"
echo "Re-test: dev-tools/diagnostic/diagnostic-all.command"
echo "Expected: section-02 + section-04 both PASS; harness reaches"
echo "0 FAIL."
echo ""
echo "Press any key to close."
read -n 1 || true
