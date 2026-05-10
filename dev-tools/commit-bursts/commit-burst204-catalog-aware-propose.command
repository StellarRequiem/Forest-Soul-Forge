#!/bin/bash
# Burst 204 — ADR-0059 catalog-aware forge propose + install validation.
#
# B203 live smoke surfaced the source-of-the-problem: the propose
# stage doesn't have the live tool catalog, so the LLM hallucinates
# tool names. A real /skills/forge call against qwen2.5-coder:7b
# produced summarize_audit_chain_integrity.v1 (chain seq #6321) —
# syntactically valid manifest referencing the non-existent
# text_summarizer.v1. Install would have happily landed an
# unrunnable skill (unknown_tool at first dispatch).
#
# Two coupled fixes.
#
# 1. Catalog injection at propose time.
#    forge.skill_forge.forge_skill accepts an optional tool_catalog
#    parameter; when provided, the engine formats a one-line-per-tool
#    summary and injects it into the user prompt with explicit
#    "use ONLY these tools, do NOT invent" framing. The HTTP
#    /skills/forge endpoint passes app.state.tool_catalog automatically.
#    Mirrored on the prompt_tool_forge side: genre_engine surfaces
#    valid archetype_tags values.
#
# 2. Install-time validation.
#    POST /skills/install cross-checks manifest.requires[] against
#    the live catalog. Unknown tools → 422 with structured
#    unknown_tools_referenced error pointing at llm_think.v1 as
#    the general-purpose fallback. force_unknown_tools=true
#    overrides for the legitimate "land a partial skill ahead of
#    installing missing tools" workflow.
#
# What ships:
#
#   docs/decisions/ADR-0059-catalog-aware-propose.md  NEW.
#     Decision + tranches T1-T7 in this burst, T8 (CLI opt-in)
#     deferred. Documents the trade-off: prompt grows ~3-5 KB
#     (still well under 32KB cap), Ollama latency goes up
#     proportionally (~12s -> ~15s on qwen2.5-coder:7b).
#
#   src/forest_soul_forge/forge/skill_forge.py  MODIFIED.
#     - forge_skill() takes tool_catalog: Any = None
#     - new _format_catalog_for_propose() helper
#     - _build_propose_prompt() accepts catalog_summary
#     - log line records catalog_injected count
#
#   src/forest_soul_forge/forge/prompt_tool_forge.py  MODIFIED.
#     - forge_prompt_tool() takes genre_engine: Any = None
#     - new _format_archetype_hints() helper
#     - _propose_user_prompt() accepts archetype_hints
#
#   src/forest_soul_forge/daemon/routers/skills_forge.py  MODIFIED.
#     - /skills/forge passes tool_catalog from app.state
#     - InstallSkillIn gains force_unknown_tools field
#     - /skills/install cross-checks requires[] vs catalog
#
#   src/forest_soul_forge/daemon/routers/tools_forge.py  MODIFIED.
#     - /tools/forge passes genre_engine from app.state
#
#   tests/unit/test_daemon_skills_forge.py  MODIFIED.
#     - test_unknown_tool_in_requires_returns_422 (B204 regression)
#     - test_unknown_tool_force_flag_allows_install (escape hatch)
#     All 8 install tests + 13 total skills_forge tests pass.
#
# Housekeeping:
#
#   data/forge/skills/staged/summarize_audit_chain_integrity.v1/
#     REMOVED. The B203 smoke proposal — staged by Alex's manual
#     UI-driven forge — referenced text_summarizer.v1 (the
#     hallucinated tool that motivated this burst). Discarding it
#     here rather than leaving stale on disk. The original
#     forge_skill_proposed event at chain seq #6321 stands as
#     forensic record of what the LLM produced; this rm is
#     filesystem hygiene, not chain rewriting.
#
# What we deliberately did NOT do:
#   - Per-step tool: reference validation. Manifest schema requires
#     every step's tool: to be in requires[], so checking requires
#     covers it. Tracked as future tightening if the schema invariant
#     slips.
#   - Catalog injection for the legacy forge.tool_forge codegen
#     engine. Different failure mode (it produces Python source,
#     not a catalog-referencing manifest). ADR-0030 T2/T3 owns.
#   - CLI opt-in for catalog injection (T8). Operator-direct UI
#     path is the priority; CLI users running 'fsf forge skill'
#     directly will eat the hallucination risk for now.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — additive parameter on
#                  forge engines, additive field on InstallSkillIn,
#                  no breaking change to existing endpoints.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

# Housekeeping: discard the B203 smoke proposal that referenced
# the hallucinated text_summarizer.v1.
if [ -d data/forge/skills/staged/summarize_audit_chain_integrity.v1 ]; then
  echo "--- discarding B203 hallucination-class smoke skill ---"
  rm -rf data/forge/skills/staged/summarize_audit_chain_integrity.v1
fi

git add docs/decisions/ADR-0059-catalog-aware-propose.md \
        src/forest_soul_forge/forge/skill_forge.py \
        src/forest_soul_forge/forge/prompt_tool_forge.py \
        src/forest_soul_forge/daemon/routers/skills_forge.py \
        src/forest_soul_forge/daemon/routers/tools_forge.py \
        tests/unit/test_daemon_skills_forge.py \
        dev-tools/commit-bursts/commit-burst204-catalog-aware-propose.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(forge): ADR-0059 catalog-aware propose + install validation (B204)

Burst 204. Source-of-the-problem fix for the hallucination class
that B203 surfaced. The B203 live UI smoke produced
summarize_audit_chain_integrity.v1 (chain seq #6321) — a
syntactically valid manifest referencing text_summarizer.v1, which
doesn't exist. The forge author flagged this gap in
forge/skill_forge.py:9-13: 'For now the LLM doesn't get the list of
available tools — that requires hooking the daemon's tool catalog at
CLI invocation time, which the next CLI tranche will add.' That
tranche never landed. ADR-0057 inherited the limitation. Closing it
here.

Two coupled fixes.

1. Catalog injection at propose time. forge.skill_forge.forge_skill
   accepts optional tool_catalog. When provided, engine formats a
   compact one-line-per-tool summary and injects with explicit
   'use ONLY these tools, do NOT invent' framing. /skills/forge
   passes app.state.tool_catalog automatically. Mirrored on
   prompt_tool_forge: genre_engine surfaces valid archetype_tags.

2. Install-time validation. /skills/install cross-checks
   manifest.requires[] against the live catalog. Unknown tools ->
   422 with structured unknown_tools_referenced error pointing at
   llm_think.v1 as the general-purpose fallback.
   force_unknown_tools=true overrides for the legitimate
   'partial skill ahead of missing tools' workflow.

Housekeeping: rm data/forge/skills/staged/summarize_audit_chain_integrity.v1
The B203 hallucinated proposal that motivated this burst. Original
forge_skill_proposed event at chain seq #6321 stands as forensic
record; this is filesystem hygiene, not chain rewriting.

ADR-0059 documents the design + the trade-off: prompt grows ~3-5KB
on the 54-tool catalog (well under 32KB cap), Ollama latency up
proportionally (~12s -> ~15s on qwen2.5-coder:7b).

Tests: 2 new B204 regression tests in test_daemon_skills_forge.py
(unknown_tool_in_requires_returns_422, unknown_tool_force_flag_allows_install).
All 64 pre-existing forge tests still pass.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — additive parameters on forge
                 engines, additive field on InstallSkillIn."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 204 complete ==="
echo "=== Forge propose now catalog-aware; install rejects hallucinated tool refs. ==="
echo "Press any key to close."
read -n 1
