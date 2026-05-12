#!/bin/bash
# Burst 230 — ship marketplace seed tools 1-5.
#
# First half of the 10-tool seed set Alex approved for the
# marketplace. All five are prompt_template_tool.v1 specs —
# data-only, read_only, instantly grantable to any agent via
# ADR-0060 runtime grants (B219-B223).
#
# Tools shipped:
#
# 1. text_summarize.v1
#    Input: text (any length)
#    Output: exactly three bullet points capturing the key ideas
#    Archetypes: briefer, knowledge_consolidator, assistant
#    Use case: compress meeting notes, articles, agent reports
#
# 2. code_explain.v1
#    Input: code + language hint ("auto" if unknown)
#    Output: prose walkthrough — purpose, structure, edge cases
#    Archetypes: software_engineer, code_reviewer, knowledge_consolidator
#    Use case: code review, onboarding, debugging
#
# 3. commit_message.v1
#    Input: diff or change description + optional scope hint
#    Output: conventional-commits-formatted message (type, scope,
#            subject, body, footer)
#    Archetypes: software_engineer, code_reviewer
#    Use case: changelog generation, release notes, SW-track triune
#
# 4. regex_explain.v1
#    Input: pattern + flavor (python | javascript | pcre | posix | auto)
#    Output: plain-English summary, token-by-token breakdown,
#            2-3 matching examples, 1-2 non-matching examples
#    Archetypes: software_engineer, code_reviewer
#    Use case: regex review, cryptic-bug debugging, teaching
#
# 5. email_draft.v1
#    Input: bullets + recipient + intent + tone
#    Output: ready-to-send email body with subject line
#    Archetypes: assistant, companion, briefer, vendor_research
#    Use case: meeting followups, status reports, vendor outreach
#    Note: drafts only, never sends — read_only side_effects
#
# All five validated via parse_spec:
#   ✓ snake_case names + valid version
#   ✓ input_schema declared correctly
#   ✓ template-var coverage 100% (every {var} maps to a property)
#   ✓ all properties listed in `required` (no default-substitution
#     gotcha — operator MUST pass every arg)
#   ✓ side_effects = read_only across the board
#
# Storage convention:
#   - Specs live at examples/tools/<name>.v1.yaml (git-tracked)
#   - data/forge/tools/installed/ is gitignored runtime state;
#     operators copy from examples/tools/ to activate
#   - examples/tools/README.md documents the convention
#   - When the marketplace launches, these become the seed entries
#
# Verification: 34 tool-forge tests pass; all 5 specs parse clean.
#
# What's next: B231 ships tools 6-10
# (tone_shift, slug_generate, sql_explain, action_items_extract,
# sentiment_analyze). Then B232 + B233 ship the 10 skills that
# compose these tools.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: ABI grows additively — 5 new tools in the
#                  forged-tool registry, zero existing call-site
#                  changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/tools/README.md \
        examples/tools/text_summarize.v1.yaml \
        examples/tools/code_explain.v1.yaml \
        examples/tools/commit_message.v1.yaml \
        examples/tools/regex_explain.v1.yaml \
        examples/tools/email_draft.v1.yaml \
        dev-tools/commit-bursts/commit-burst230-tools-1-5.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(tools): marketplace seed tools 1-5 (B230)

Burst 230. First half of the 10-tool seed catalog Alex approved
for marketplace shipping. All five are prompt_template_tool.v1
specs — data-only, read_only, instructional templates per the
B210 substrate.

  1. text_summarize.v1 — text -> 3 concise bullets
  2. code_explain.v1 — code + language -> plain English walkthrough
  3. commit_message.v1 — diff -> conventional-commits message
  4. regex_explain.v1 — pattern + flavor -> meaning + examples
  5. email_draft.v1 — bullets + recipient/intent/tone -> ready email

All five parse cleanly via parse_spec, 100% template-var coverage,
all properties required (no .format default-substitution gotcha).

Registration: daemon lifespan walks data/forge/tools/installed/
at startup; restart picks up the 5 new tools. Grant per-agent via
the ADR-0060 pane on the Agents tab.

34 tool-forge tests pass.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: ABI grows additively — 5 new tools."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 230 complete ==="
echo "=== 5 of 10 tools shipped. B231 ships 6-10 (tone_shift, slug, sql, action_items, sentiment). ==="
echo "Press any key to close."
read -n 1
