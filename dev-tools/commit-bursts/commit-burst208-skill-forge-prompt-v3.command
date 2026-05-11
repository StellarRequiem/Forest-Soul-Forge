#!/bin/bash
# Burst 208 — Skill Forge prompt v3 (ref-scope rules) + exception capture.
#
# B207 (failure-mode diagnostics) shipped a stronger prompt that forbade
# YAML flow-mapping syntax for fields containing ${...} expressions.
# That fixed the original parse failure mode Alex observed.
#
# Live re-smoke surfaced the NEXT failure mode: the LLM produced
# block-style YAML (B207's prompt fix worked) but used bare
# ${audit_chain_path} where ${inputs.audit_chain_path} is required:
#
#     inputs:
#       audit_chain_path: string
#     steps:
#       - id: load_audit_chain
#         tool: code_read.v1
#         args:
#           file_path: ${audit_chain_path}    # ← BAD: bare name
#
# The parser's _check_refs raises ManifestError because only the
# whole `inputs` object is in scope at top level, not its individual
# fields. To reference a field you must say ${inputs.audit_chain_path}.
#
# The pre-B208 propose-prompt described ref scope as "bare name +
# .field chain — refer to step ids, `inputs`, or `each`" which the LLM
# read as license to drop the `inputs.` prefix. Pre-B208 also let
# `inputs:` show up as a STEP-LEVEL field (silently ignored), then
# the bare ref blew up downstream — the underlying confusion was the
# LLM treating step-level `inputs:` as a real thing.
#
# B208 fixes three coupled gaps.
#
# 1. _PROPOSE_SYSTEM gains a CRITICAL REFERENCE SYNTAX RULE section
#    (PROMPT_VERSION 2 -> 3) with explicit correct/wrong examples:
#       Correct: file_path: ${inputs.audit_chain_path}
#       Wrong:   file_path: ${audit_chain_path}
#       Correct: prompt: ${summarize.out.text}
#       Wrong:   prompt: ${summarize}
#    The "expression language" section is tightened to spell out the
#    three valid bare names — `inputs.<field>`, `<step_id>.out`,
#    `each` — instead of the ambiguous "bare name + .field chain."
#
# 2. The step-shape callout is sharpened: each step has EXACTLY
#    {id, tool, args, when, unless} — no `inputs:` field — with an
#    explicit "Do NOT put `inputs:` on a step" sentence. Pre-B208 the
#    LLM emitted `inputs: ${prior.out}` on a step (silently ignored
#    by the parser, but symptomatic of the LLM's mental model
#    confusion).
#
# 3. forge_skill()'s parse-failure except block now captures the
#    actual exception type + ManifestError.path + ManifestError.detail
#    into forge.log. Pre-B208 the log only said "parse_manifest
#    raised" without the actual failure reason — the operator had to
#    cross-reference the HTTP 422 response detail with the raw YAML
#    in the quarantine dir. Now forge.log alone is enough to diagnose.
#
# Live test path:
#   1. `live-test-b207-skill-forge.command` (existing) re-runs the
#      same forge with the prompt that hit B207's residual failure.
#   2. With v3 prompt + same Ollama model the LLM should produce
#      `${inputs.audit_chain_path}` not `${audit_chain_path}`.
#   3. If it doesn't (LLM is non-deterministic), forge.log now
#      records the exact ManifestError that fired, so the operator
#      has full visibility into the failure shape without code reading.
#
# Side note: B208 does NOT touch prompt_tool_forge.py. The tool forge
# manifests don't use the expression substrate at all — they're flat
# spec.yaml files describing input_schema + prompt_template. Different
# failure surface.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — forge.log gains diagnostic
#                  fields; HTTP 422 response unchanged.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/forge/skill_forge.py \
        dev-tools/commit-bursts/commit-burst208-skill-forge-prompt-v3.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(forge): skill-forge prompt v3 + exception capture in forge.log (B208)

Burst 208. B207 fixed the YAML flow-mapping vs expression-braces clash
in the propose prompt. Live re-smoke surfaced the NEXT failure mode:
the LLM produced clean block-style YAML but used bare expression refs
like \\\\\${audit_chain_path} where \\\\\${inputs.audit_chain_path} is required.

The parser only puts the whole \\\\\`inputs\\\\\` object in scope at top
level, not its individual fields — bare refs to input field names
fail at _check_refs. The pre-B208 prompt described the rule
ambiguously as 'bare name + .field chain', which the LLM read as
license to drop the \\\\\`inputs.\\\\\` prefix.

Three coupled fixes.

1. _PROPOSE_SYSTEM gains a CRITICAL REFERENCE SYNTAX RULE section
   (PROMPT_VERSION 2 -> 3) with explicit correct/wrong examples for
   \\\\\${inputs.X} and \\\\\${step.out.field}. The 'expression language'
   section is tightened to spell out the three valid bare names —
   inputs.<field>, <step_id>.out, each — instead of the ambiguous
   'bare name + .field chain.'

2. Step-shape callout sharpened: each step has EXACTLY
   {id, tool, args, when, unless} — no \\\\\`inputs:\\\\\` field — with an
   explicit 'Do NOT put inputs: on a step' sentence. The B207 smoke
   produced a step with bare \\\\\`inputs: \\\\\${prior.out}\\\\\` (silently
   ignored by the parser, but symptomatic of LLM mental model
   confusion).

3. forge_skill()'s parse-failure except block now captures the
   actual exception type + ManifestError.path + ManifestError.detail
   into forge.log. Pre-B208 the log only said 'parse_manifest
   raised' — operator had to cross-reference HTTP 422 response
   detail with raw YAML. Now forge.log alone is enough to diagnose.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — forge.log gains diagnostic
                 fields; HTTP 422 response unchanged."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 208 complete ==="
echo "=== Prompt v3 active; forge.log captures full parse exception. ==="
echo "Press any key to close."
read -n 1
