#!/bin/bash
# Burst 207 — Skill Forge failure-mode diagnostics + prompt hardening.
#
# B204 (catalog-aware propose) made the LLM produce more elaborate
# YAML. On Alex's re-smoke this surfaced an edge case: the LLM emitted
# YAML flow-mapping syntax with embedded ${...} expression refs, e.g.
#
#     output: { summary: ${summarize.output.text} }
#
# YAML flow-mappings use the same { } delimiters as the expression
# syntax. They CLASH. The parser sees nested { it doesn't expect and
# raises ManifestError. The forge endpoint returned 422 with the
# parser's error string — but Alex couldn't tell what the LLM had
# actually produced because the raw output was thrown away.
#
# Pre-B207 the engine's order of operations was:
#   1. provider.complete -> raw YAML
#   2. parse_manifest(raw) -> SkillDef
#   3. mkdir staged_dir
#   4. write manifest.yaml + forge.log
#
# Step 2 raising left no on-disk artifact. The forge.log only existed
# in the SkillForgeResult object that never got returned, then went
# to gc. Operator saw "manifest_validation_failed: <error>" and had
# no way to inspect the raw output.
#
# B207 fixes three coupled issues.
#
# 1. forge.log + manifest_raw.yaml written BEFORE parse_manifest.
#    Engine now stages a quarantine dir (name = name_override or
#    timestamp-keyed fallback) and writes the raw cleaned YAML + log
#    immediately after the LLM call returns. parse_manifest then
#    runs; on success the engine relocates the quarantine into the
#    canonical name.vversion dir and keeps the raw file alongside
#    the parsed manifest. On failure, the quarantine dir stays —
#    operator can read manifest_raw.yaml, edit it by hand, and
#    re-install via /skills/install pointing at the dir.
#
# 2. _PROPOSE_SYSTEM prompt strengthened (version bumped 1 -> 2).
#    New CRITICAL YAML STYLE RULE section explicitly forbids
#    flow-style YAML for output/inputs/args/properties/steps and
#    explains the ${...} vs {} clash with concrete correct/wrong
#    examples. Also added "ALWAYS versioned" caveat for tool refs
#    (llm_think.v1 not bare llm_think) to head off the parallel
#    failure mode.
#
# 3. POST /skills/forge 422 response now includes quarantine_dir
#    and forge_log_excerpt (last 1200 chars of the log). The
#    skills.js modal renders these in the error display: a code-
#    formatted path the operator can copy + a collapsible
#    <details> section showing what the LLM actually produced.
#
# What ships:
#
#   src/forest_soul_forge/forge/skill_forge.py  MODIFIED.
#     - PROMPT_VERSION 1 -> 2
#     - _PROPOSE_SYSTEM gains "CRITICAL YAML STYLE RULE" + versioned
#       tool ref caveat
#     - forge_skill() reorders: quarantine + log write BEFORE parse;
#       on success, relocate to canonical name.vversion dir
#
#   src/forest_soul_forge/daemon/routers/skills_forge.py  MODIFIED.
#     - 422 ManifestError handler now reads the most recent
#       quarantine dir's forge.log + path and surfaces them in
#       the structured detail
#
#   frontend/js/skills.js  MODIFIED.
#     - Modal forge error display gains structured rendering:
#       quarantine_dir path (code-formatted) + collapsible
#       <details> showing forge_log_excerpt. Falls back to plain
#       error text for non-422 / non-manifest_validation_failed
#       failures.
#
# What we deliberately did NOT do:
#   - Equivalent change for prompt_tool_forge / /tools/forge.
#     Tool forge has a different failure mode (parse_spec validates
#     a flat schema, no ${} expressions in spec.yaml). If a similar
#     issue surfaces there it's a separate burst.
#   - Auto-retry with a tighter prompt on parse failure. Operator
#     should see what the LLM produced and decide whether to
#     re-forge with a more specific description or edit the raw
#     by hand. Auto-retry hides the failure mode.
#   - Tests for the quarantine-on-failure path. The flow is best
#     verified live (the failure case is LLM-dependent); the
#     existing test_invalid_manifest_returns_422 covers the 422
#     code path, just doesn't assert quarantine_dir is in detail
#     since that requires a real engine run not the unit-stub
#     bypass.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — 422 response gains two
#                  optional fields, callers that don't read them
#                  see no difference.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/forge/skill_forge.py \
        src/forest_soul_forge/daemon/routers/skills_forge.py \
        frontend/js/skills.js \
        dev-tools/commit-bursts/commit-burst207-forge-failure-diagnostics.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(forge): skill-forge failure-mode diagnostics + prompt hardening (B207)

Burst 207. B204 (catalog-aware propose) made the LLM produce more
elaborate YAML. On Alex's re-smoke this hit an edge case: the LLM
emitted YAML flow-mapping syntax with embedded \${...} expression
refs, e.g. \`output: { summary: \${summarize.output.text} }\`.

YAML flow-mappings use the same { } delimiters as the expression
syntax. They CLASH — the parser sees nested { it doesn't expect and
raises ManifestError. The forge endpoint returned 422 but the raw
LLM output was thrown away (pre-B207 the engine staged the manifest
AFTER parse_manifest succeeded, so parse failure left nothing on
disk and no way to debug what the LLM had produced).

Three coupled fixes.

1. Engine writes forge.log + manifest_raw.yaml BEFORE parse_manifest.
   On parse failure the quarantine dir stays so operator can read
   the raw YAML, edit by hand, and re-install via /skills/install.
   On parse success the quarantine relocates into the canonical
   name.vversion dir and keeps the raw file alongside.

2. _PROPOSE_SYSTEM prompt strengthened (PROMPT_VERSION 1 -> 2).
   New CRITICAL YAML STYLE RULE section explicitly forbids
   flow-style YAML for output/inputs/args/properties/steps with
   concrete correct/wrong examples explaining the \${...} vs {}
   clash. Also added 'ALWAYS versioned' caveat for tool refs to
   head off the parallel failure mode.

3. POST /skills/forge 422 response now carries quarantine_dir +
   forge_log_excerpt (last 1200 chars). skills.js modal renders
   these structured: path is code-formatted, log is a collapsible
   <details> section. Operator can immediately see what the LLM
   produced and where the raw file is.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — 422 response gains two optional
                 fields; callers that don't read them see no change."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 207 complete ==="
echo "=== Forge failure mode now self-diagnostic; prompt hardened against YAML flow-style. ==="
echo "Press any key to close."
read -n 1
