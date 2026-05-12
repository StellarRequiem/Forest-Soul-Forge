#!/bin/bash
# Burst 231 — ship marketplace seed tools 6-10.
#
# Closes the 10-tool catalog Alex approved in the session-start
# planning turn. Same authoring conventions as B230 (data-only,
# read_only, instructional templates, all-properties-required).
#
# Tools shipped:
#
# 6. tone_shift.v1
#    Input: text + target_tone + preserve
#    Output: rewritten text, ~same length, preserved specifics intact
#    Use case: shift Slack messages, status updates, drafts between
#              formal/casual/diplomatic/direct registers
#
# 7. slug_generate.v1
#    Input: title + max_length
#    Output: lowercase, hyphen-separated, ASCII-folded URL slug
#    Use case: blog URLs, file names, audit-doc filenames, branch names
#
# 8. sql_explain.v1
#    Input: sql + dialect
#    Output: structured analysis — what it does, tables touched, risks
#    Use case: code review, debugging slow queries, dialect-aware
#              static analysis without executing
#
# 9. action_items_extract.v1
#    Input: text + known_attendees
#    Output: YAML list of {owner, action, due_date_hint, source_quote}
#    Use case: meeting followup, project tracker hydration, status
#              report generation. Empty list when no actions present.
#
# 10. sentiment_analyze.v1
#     Input: text + target (overall | mood | toward-X | etc.)
#     Output: YAML {sentiment, confidence, rationale, key_phrases}
#     Use case: support ticket triage, community-feedback summary,
#               agent self-report distress monitoring
#
# All five validated via parse_spec — 100% template-var coverage,
# all properties listed in required, side_effects=read_only.
#
# Net seed-tool catalog after this burst:
#   examples/tools/
#     ├── README.md                       (B230)
#     ├── code_explain.v1.yaml            (B230)
#     ├── commit_message.v1.yaml          (B230)
#     ├── email_draft.v1.yaml             (B230)
#     ├── regex_explain.v1.yaml           (B230)
#     ├── text_summarize.v1.yaml          (B230)
#     ├── tone_shift.v1.yaml              (B231)
#     ├── slug_generate.v1.yaml           (B231)
#     ├── sql_explain.v1.yaml             (B231)
#     ├── action_items_extract.v1.yaml    (B231)
#     └── sentiment_analyze.v1.yaml       (B231)
#
# Activation: `cp examples/tools/*.yaml data/forge/tools/installed/`
# then daemon restart. Once registered, grant per-agent via the
# Tool grants pane on the Agents tab (ADR-0060 T6, B223).
#
# What's next: B232 ships skills 1-5, B233 ships skills 6-10.
# Skills compose these tools (and existing builtins like code_read,
# audit_chain_verify, memory_recall) into multi-step workflows.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: ABI grows additively — 5 new tools, zero
#                  existing call-site changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/tools/tone_shift.v1.yaml \
        examples/tools/slug_generate.v1.yaml \
        examples/tools/sql_explain.v1.yaml \
        examples/tools/action_items_extract.v1.yaml \
        examples/tools/sentiment_analyze.v1.yaml \
        dev-tools/commit-bursts/commit-burst231-tools-6-10.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(tools): marketplace seed tools 6-10 (B231)

Burst 231. Closes the 10-tool seed catalog Alex approved.

  6. tone_shift.v1 — text + tone -> rewritten, ~same length
  7. slug_generate.v1 — title + max_length -> URL slug
  8. sql_explain.v1 — SQL + dialect -> analysis + risks
  9. action_items_extract.v1 — text + attendees -> YAML list of items
  10. sentiment_analyze.v1 — text + target -> sentiment + confidence

All five parse via parse_spec, 100% template-var coverage,
side_effects=read_only.

Net catalog: 10 tools in examples/tools/, README documents the
authoring conventions and activation path.

What's next: B232 + B233 ship the 10 skills that compose these
tools into multi-step workflows.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: additive — 5 new tools."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 231 complete ==="
echo "=== 10 of 10 tools shipped. B232 + B233 ship 10 skills composing them. ==="
echo "Press any key to close."
read -n 1
