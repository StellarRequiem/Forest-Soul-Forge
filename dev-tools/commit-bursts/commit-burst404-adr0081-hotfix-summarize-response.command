#!/bin/bash
# Burst 404 - ADR-0081 HOTFIX-5: text_summarize output field name.
#
# Surfaced on B403's re-verify: skill ran verify_chain + recall_prior +
# summarize (full LLM output ~20s with model=qwen2.5-coder:7b), but
# step `record` failed because my manifest interpolated
# `${summarize.summary}` — the field doesn't exist.
#
# text_summarize.v1 (via PromptTemplateToolBase) produces an output
# dict with keys: {response, model, task_kind, elapsed_ms}. The
# generated text is `response`, not `summary`. Two references in
# the manifest were wrong.
#
# While here, also fixed the input arg name (target_length ->
# target_words) which had been getting ignored silently — defaulted
# to 150 words. Now correctly requests 400 words for the punch list.
#
# What this commit adds:
#
# 1. examples/skills/wiring_audit.v1.yaml — three small fixes:
#    - summarize args: target_length -> target_words (+ comment).
#    - record content: ${summarize.summary} -> ${summarize.response}.
#    - skill output: summary_text: ${summarize.summary} -> .response.
#    - Comment noting the actual output schema field.
#
# Operator sync step (gitignored install dir):
#   cp examples/skills/wiring_audit.v1.yaml data/forge/skills/installed/
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: ${summarize.summary} resolves to undefined →
#     record step's content interpolation fails → status=failed.
#     This is the 4th hotfix in a row; each fixes a separate
#     manifest-vs-runtime drift.
#   Prove non-load-bearing: three text replacements in one yaml.
#   Prove alternative: response IS the literal-correct field name
#     per PromptTemplateToolBase. summary doesn't exist. No alt.
#
# Reflection on the hotfix cascade (B400-B404):
#   B400 — genres.yaml missed wiring_sentinel role entry.
#   B401 — wrapper used /skills/call instead of /skills/run.
#         + installed/ dir not synced (gitignored).
#   B402 — style: punch_list (not in text_summarize enum).
#   B403 — memory_write content as dict instead of string.
#   B404 — summarize.summary instead of summarize.response.
#
#   Each was a missed-cross-reference between manifest and the
#   tool/endpoint actually wrapping it. Section-15 should be
#   extended to validate every step's args + output references
#   against the wrapped tool's input_schema + output keys. That's
#   the next operator-queue item this hotfix cascade surfaced.
#
# Verification after this commit lands:
#   1. (already-synced installed dir)
#   2. bash dev-tools/run-wiring-audit.command
#      Expected: status=succeeded. output non-null. output.summary_text
#      contains the LLM response. Lineage memory gains a
#      wiring_audit_outcome entry.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/wiring_audit.v1.yaml \
        dev-tools/commit-bursts/commit-burst404-adr0081-hotfix-summarize-response.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(skill): text_summarize output field name (ADR-0081 HOTFIX-5, B404)

Burst 404. HOTFIX-5. Skill ran verify_chain + recall_prior +
summarize (full LLM output ~20s), then failed at record because
my manifest interpolated \${summarize.summary} — the field
doesn't exist. text_summarize.v1 outputs {response, model,
task_kind, elapsed_ms}. The generated text is \`response\`.

Three fixes in examples/skills/wiring_audit.v1.yaml:
  - summarize args: target_length -> target_words (was being
    silently ignored, defaulted to 150 words).
  - record content interpolation: .summary -> .response.
  - skill output block: .summary -> .response.

Hotfix cascade reflection (B400-B404):
  B400 genres.yaml missed wiring_sentinel.
  B401 wrapper /skills/call -> /skills/run + installed/ sync.
  B402 style: punch_list (not in text_summarize enum).
  B403 memory_write content dict -> string.
  B404 summarize.summary -> summarize.response.

Each was a missed cross-reference between manifest + the tool's
actual schema. Section-15 should validate every step's args and
output refs against wrapped tool's input_schema + output keys.
Next operator-queue item this cascade surfaced.

Operator sync (gitignored): cp examples/skills/wiring_audit.v1.yaml
  data/forge/skills/installed/

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: summary -> undefined; record interp fails; status=failed.
  Prove non-load-bearing: three text replacements in one yaml.
  Prove alternative: response is the literal-correct field name."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 404 complete - ADR-0081 HOTFIX-5 shipped ==="
echo "=========================================================="
echo "Next: bash dev-tools/run-wiring-audit.command"
echo ""
echo "Press any key to close."
read -n 1 || true
