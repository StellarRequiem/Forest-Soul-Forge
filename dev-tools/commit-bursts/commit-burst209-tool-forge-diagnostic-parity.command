#!/bin/bash
# Burst 209 — Tool Forge engine parity with B207+B208 patterns.
#
# B207 + B208 hardened the skill-forge engine against three failure
# modes: (1) LLM markdown fences poisoning the parser, (2) parse
# failure leaving no on-disk artifact, (3) parse exception text only
# in the 422 response. Live tool-forge smoke today hit (1) exactly:
# qwen2.5-coder produced its YAML wrapped in ```yaml ... ``` despite
# the propose-system saying "no markdown fences." The tool-forge
# engine had no _strip_fences pass, so parse_spec saw raw backticks
# and yaml.safe_load failed at line 1 column 1.
#
# B209 mirrors B207+B208 for the prompt-tool-forge engine.
#
# 1. _strip_fences() added (same shape as forge.skill_forge), called
#    on the raw LLM output before parse_spec.
#
# 2. _PROPOSE_SYSTEM gains a CRITICAL OUTPUT FORMAT section
#    (PROMPT_VERSION 1 -> 2). The earlier "no markdown fences" was
#    a single sentence the LLM ignored. The new section is explicit:
#    "the very first character of your reply is a YAML key name."
#    Belt-and-suspenders with the engine's strip pass.
#
# 3. Quarantine pattern: spec_raw.yaml + forge.log get written
#    BEFORE parse_spec. On parse failure, the quarantine dir stays
#    so the operator can read what the LLM produced. On success,
#    the engine relocates to the canonical name.vversion dir and
#    keeps spec_raw.yaml alongside the parsed spec.yaml. Pre-B209,
#    a parse failure left NO on-disk artifact at all.
#
# 4. Parse-failure except block captures the ToolSpecError.path +
#    .detail (or the exception repr for non-ToolSpecError exceptions)
#    into forge.log. Pre-B209 the log line just said "parse_spec
#    called" with no failure info; the only place the exception text
#    surfaced was the 422 response detail.
#
# What we deliberately did NOT do:
#   - Modify the /tools/forge router 422 handler. The router already
#     surfaces a useful detail; the new quarantine artifact is on disk
#     for operators who want to read it but doesn't need to be in
#     the response body. (A follow-up burst could add quarantine_dir
#     to the response if the frontend's tool-forge error display
#     wants a code-formatted path like the skill-forge one.)
#   - Add tests for the quarantine-on-failure path. The existing
#     34-test surface covers the success path + 422 paths; the
#     quarantine flow is best verified live (LLM-dependent output).
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — forge.log gains diagnostic
#                  fields; spec.yaml output unchanged.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/forge/prompt_tool_forge.py \
        dev-tools/commit-bursts/commit-burst209-tool-forge-diagnostic-parity.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(forge): tool-forge diagnostic parity with B207+B208 (B209)

Burst 209. B207 + B208 hardened the skill-forge engine against three
failure modes: LLM markdown fences, parse failure leaving no on-disk
artifact, parse exception text only in the 422 response. Live
tool-forge smoke today hit the first one — qwen2.5-coder wrapped its
YAML in fenced markdown despite the no-fence instruction, parse_spec
saw raw backticks, yaml.safe_load failed at line 1 column 1.

B209 mirrors B207+B208 for prompt_tool_forge.

1. _strip_fences() added (same shape as skill_forge), called before
   parse_spec.

2. _PROPOSE_SYSTEM gains CRITICAL OUTPUT FORMAT section
   (PROMPT_VERSION 1 -> 2): 'the very first character of your reply
   is a YAML key name.' Belt-and-suspenders with the engine strip.

3. Quarantine pattern: spec_raw.yaml + forge.log written BEFORE
   parse_spec. On parse failure quarantine dir stays for operator
   inspection. On success engine relocates to canonical
   name.vversion dir, keeps spec_raw.yaml alongside parsed spec.yaml.

4. Parse-failure except block captures ToolSpecError.path + .detail
   (or exception repr) into forge.log.

34 tool-forge tests pass post-change.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — forge.log gains diagnostic
                 fields; spec.yaml output unchanged."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 209 complete ==="
echo "=== Tool forge now handles fences + quarantines + logs exceptions. ==="
echo "Press any key to close."
read -n 1
