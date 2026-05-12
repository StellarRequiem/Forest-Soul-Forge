#!/bin/bash
# Burst 232 — ship marketplace seed skills 1-5.
#
# First half of the 10-skill seed set. Each skill composes the new
# B230+B231 prompt-template tools with existing builtins
# (code_read, audit_chain_verify, memory_recall, llm_think) into
# a multi-step workflow. All five validated via parse_manifest with
# 100% ref-scope coverage post-B208.
#
# Skills shipped:
#
# 1. morning_briefing.v1 — 3 steps
#    audit_chain_verify -> memory_recall -> text_summarize
#    Inputs: focus (one-word steering hint)
#    Output: 3-bullet daily briefing + chain integrity + memory hit
#            count. The status_reporter/operator_companion's morning
#            ritual.
#
# 2. code_review_quick.v1 — 3 steps
#    code_read -> code_explain -> llm_think (critic)
#    Inputs: file_path, language
#    Output: explanation + specific concerns bullet list +
#            "Overall:" one-line verdict. First-pass triage for a
#            software_engineer agent.
#
# 3. meeting_followup.v1 — 2 steps
#    action_items_extract -> email_draft
#    Inputs: notes, recipient, known_attendees, tone
#    Output: extracted action items + ready-to-paste email body.
#    Read_only end-to-end; agent never sends.
#
# 4. bug_report_polish.v1 — 3 steps
#    code_read -> code_explain -> email_draft
#    Inputs: raw_note, suspect_file, language, tracker_recipient
#    Output: formal bug report (repro + expected/actual + initial
#            hypothesis) + the suspect code's static-analysis
#            explanation as context.
#
# 5. commit_changelog.v1 — 2 steps
#    commit_message -> text_summarize
#    Inputs: diff, scope_hint, audience (users | operators | devs)
#    Output: conventional commit message + audience-tuned 3-bullet
#            changelog entry. Avoids inventing changes by chaining
#            through commit_message's structured output.
#
# All five validated post-B208 ref-scope rules:
#   ✓ All ${var} refs are either ${inputs.X} or ${step_id.out.field}
#   ✓ All declared step ids referenced correctly
#   ✓ Output mapping references valid step.out fields or input names
#   ✓ requires[] lists every distinct tool the steps touch
#
# Activation: `cp examples/skills/{name}.v1.yaml data/forge/skills/installed/`
# + daemon restart. The kernel's lifespan walks the install dir
# and registers each manifest. Agents whose constitution or runtime
# grants cover the required tools can then dispatch via
# POST /agents/{id}/skills/run.
#
# What's next: B233 ships skills 6-10
# (agent_activity_digest, memory_consolidate, incident_first_pass,
# release_notes, agent_introspect).
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: ABI grows additively — 5 new skills in the
#                  shipped-examples directory, zero existing
#                  call-site changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/morning_briefing.v1.yaml \
        examples/skills/code_review_quick.v1.yaml \
        examples/skills/meeting_followup.v1.yaml \
        examples/skills/bug_report_polish.v1.yaml \
        examples/skills/commit_changelog.v1.yaml \
        dev-tools/commit-bursts/commit-burst232-skills-1-5.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(skills): marketplace seed skills 1-5 (B232)

Burst 232. First half of the 10-skill seed set. Each composes
B230/B231 prompt-template tools with existing builtins.

  1. morning_briefing.v1 (3 steps) — audit_chain_verify ->
     memory_recall -> text_summarize. Status-reporter's morning
     ritual.
  2. code_review_quick.v1 (3 steps) — code_read -> code_explain ->
     llm_think critic. First-pass triage for a software_engineer.
  3. meeting_followup.v1 (2 steps) — action_items_extract ->
     email_draft. Ready-to-paste followup email. Never sends.
  4. bug_report_polish.v1 (3 steps) — code_read -> code_explain ->
     email_draft. Polishes a rough note + suspect file into a
     formal bug report.
  5. commit_changelog.v1 (2 steps) — commit_message ->
     text_summarize. Audience-tuned changelog entry chained
     through structured commit-message output.

All five parse cleanly via parse_manifest with post-B208 ref-scope
validation: every ref is \${inputs.X} or \${step.out.field}; no
bare-name refs; output mapping references valid step outputs.

Activation: cp examples/skills/*.yaml data/forge/skills/installed/
+ daemon restart.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: additive — 5 new shipped skill examples."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 232 complete ==="
echo "=== 5 of 10 skills shipped. B233 ships the second half. ==="
echo "Press any key to close."
read -n 1
