#!/bin/bash
# Burst 210 — prompt_tool_forge propose-quality fix: teach the runtime model.
#
# B209 made the tool-forge engine structurally bulletproof (fence-
# stripping, quarantine-on-failure, exception capture). The first
# live end-to-end smoke landed a forged tool successfully — installed
# and registered — but the prompt_template the LLM produced was
# useless:
#
#   description: "Rewrite a paragraph of text as exactly three
#                 concise bullet points capturing the most important
#                 ideas."
#   prompt_template: |
#     - {paragraph}
#     - {paragraph}
#     - {paragraph}
#
# After str.format(paragraph=<input>) substitution this becomes three
# identical bullets of the same input text — a useless prompt that
# tells the downstream LLM nothing about what to do.
#
# Root cause: qwen2.5-coder:7b doesn't understand the prompt-template
# tool's runtime model. It thinks the template IS the answer with
# placeholders. The pre-B210 propose-system described required keys
# and the format() substitution mechanic but never explained that
# the substituted text becomes a NEW prompt sent to ANOTHER LLM at
# execute() time.
#
# B210 adds an explicit "HOW THIS TOOL ACTUALLY RUNS" section to
# _PROPOSE_SYSTEM (PROMPT_VERSION 2 -> 3) with a four-step runtime
# walkthrough and a concrete WRONG/RIGHT example pair using the
# exact failure mode from the B209 smoke:
#
#   WRONG: "- {paragraph}\n- {paragraph}\n- {paragraph}"
#   RIGHT: a clear instruction ("Summarize the paragraph below as
#          exactly three concise bullet points..."), then the
#          {paragraph} placeholder as context, then a
#          "Three bullet points:" cue to start the LLM's response.
#
# Also tightens the OUTPUT REQUIREMENTS section with one more line:
# "The prompt_template should be an INSTRUCTION followed by the {var}
# placeholders for context, NOT a pre-written answer with placeholders.
# See the CONCRETE EXAMPLE above."
#
# What we deliberately did NOT do:
#   - Add a heuristic that rejects templates whose substituted text
#     contains no instruction keywords ("summarize," "translate,"
#     "explain," etc.). The prompt fix is sufficient to test; a
#     mechanical reject-rule risks over-constraint (some legitimate
#     templates ARE just context with a leading instruction).
#   - Add a runtime sanity check that the downstream LLM's response
#     differs from the substituted prompt. Same reason — easy to
#     get wrong, and B210's prompt fix is the correct primary defense.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — prompt string update only.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/forge/prompt_tool_forge.py \
        dev-tools/commit-bursts/commit-burst210-prompt-tool-runtime-model.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(forge): teach prompt_tool_forge propose the runtime model (B210)

Burst 210. B209 made tool-forge structurally bulletproof; first live
smoke landed a forged tool but the prompt_template was useless:

  description: 'rewrite as three concise bullet points'
  prompt_template: |
    - {paragraph}
    - {paragraph}
    - {paragraph}

After str.format substitution this becomes three identical bullets of
the same input — a useless prompt. qwen2.5-coder:7b didn't understand
that the substituted text gets sent to ANOTHER LLM at execute() time;
it thought the template IS the answer with placeholders.

B210 adds 'HOW THIS TOOL ACTUALLY RUNS' to _PROPOSE_SYSTEM
(PROMPT_VERSION 2 -> 3): explicit four-step runtime walkthrough plus
a concrete WRONG/RIGHT example using the exact failure mode the
B209 smoke produced.

WRONG: '- {paragraph}\\\\n- {paragraph}\\\\n- {paragraph}'
RIGHT: 'Summarize the paragraph below as exactly three concise
       bullet points...\\\\n\\\\nParagraph:\\\\n{paragraph}\\\\n\\\\n
       Three bullet points:'

Tightens OUTPUT REQUIREMENTS with: 'The prompt_template should be
an INSTRUCTION followed by the {var} placeholders for context, NOT
a pre-written answer with placeholders.'

34 tool-forge tests pass post-change.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — prompt string update only."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 210 complete ==="
echo "=== prompt_template_tool forge now teaches the runtime model. ==="
echo "Press any key to close."
read -n 1
