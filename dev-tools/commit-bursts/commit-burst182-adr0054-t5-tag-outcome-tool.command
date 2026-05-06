#!/bin/bash
# Burst 182 — ADR-0054 T5 (tool side) — memory_tag_outcome.v1 +
# ToolContext.procedural_shortcuts wiring.
#
# T5 has two halves: the reinforcement tool (this burst) and the
# chat-tab thumbs UI that drives it (B183, queued). Shipping the
# tool side first means the substrate exists and is testable end-
# to-end via the API even before the UI lands.
#
# What ships:
#
#   src/forest_soul_forge/tools/builtin/memory_tag_outcome.py (NEW):
#     MemoryTagOutcomeTool. Args: shortcut_id (required),
#     outcome ∈ {good, bad, neutral} (required), note (optional,
#     ≤300 chars). Output: shortcut_id, outcome,
#     new_success_count, new_failure_count,
#     new_reinforcement_score, soft_deleted (True iff score<0).
#
#     Behavior:
#       - good    → table.strengthen(by=1)
#       - bad     → table.weaken(by=1)
#       - neutral → no counter change but tool still emits
#                   audit-visible tool_call_succeeded so an
#                   operator's deliberate non-tag is itself
#                   signal ('I saw this and chose neither')
#
#     side_effects: read_only — mutating per-instance counters
#       is the agent's own state per ADR-0001 D2 (identity
#       invariance: this table is per-instance state, not
#       identity). Choosing read_only avoids unwanted approval
#       gates on operator gestures.
#
#     required_initiative_level: L2 — operator-initiated by
#       design. The chat-tab thumbs widget (T5b) is the load-
#       bearing caller; the agent never self-reinforces without
#       operator routing. L2 floor matches 'operator-supervised
#       reactive'.
#
#     Refuses cleanly when:
#       - ctx.procedural_shortcuts is None (pre-T6 daemons /
#         test contexts) — substrate not wired
#       - shortcut_id doesn't exist
#       - shortcut_id belongs to a different agent — cross-
#         agent tagging is a privilege-escalation surface,
#         refused structurally
#
#   src/forest_soul_forge/tools/base.py:
#     - ToolContext gains procedural_shortcuts field. Populated
#       by the dispatcher's GO leg + resume_approved leg from
#       self.procedural_shortcuts_table. None when the substrate
#       is unwired (default for pre-T6 daemons); the new tool
#       refuses cleanly in that case.
#
#   src/forest_soul_forge/tools/dispatcher.py:
#     - Both ToolContext construction sites (GO leg +
#       resume_approved) updated to pass
#       procedural_shortcuts=self.procedural_shortcuts_table.
#       No new dispatcher fields — reuses the table handle T3
#       wired in B180.
#
#   src/forest_soul_forge/tools/builtin/__init__.py:
#     - MemoryTagOutcomeTool import + __all__ entry +
#       register_builtins call (alongside the other memory_*
#       tools, after MemoryFlagContradictionTool).
#
#   config/tool_catalog.yaml:
#     - memory_tag_outcome.v1 catalog entry. side_effects=read_only,
#       required_initiative_level=L2, archetype_tags=[companion,
#       assistant]. Full input_schema with shortcut_id, outcome
#       enum, note (≤300 chars).
#
#   tests/unit/test_memory_tag_outcome.py (NEW):
#     20 unit tests across three classes:
#
#       TestValidation (8 tests):
#         - shortcut_id required + non-empty string
#         - outcome required + must be in valid enum
#         - each valid outcome accepted
#         - note must be string when provided
#         - note length capped at MAX_NOTE_LEN
#         - note at the cap is OK (boundary)
#
#       TestExecute (10 tests):
#         - good → strengthen +1, counter visible in output
#         - bad → weaken +1
#         - neutral → no counter change
#         - soft_deleted=True iff reinforcement_score < 0
#         - refuses when ctx.procedural_shortcuts is None
#         - refuses unknown shortcut_id
#         - refuses cross-agent tag (privilege escalation
#           surface)
#         - output metadata.note populated when provided
#         - side_effect_summary format
#         - multiple strengthen calls accumulate
#
#       TestToolMetadata (3 tests):
#         - name + version
#         - side_effects == read_only (per ADR-0001 D2 framing)
#         - required_initiative_level == L2
#
# Per ADR-0044 D3: zero kernel ABI changes. ToolContext gains
# an optional field defaulting to None; existing tools and
# tests pass without modification (53/53 in
# test_tool_dispatcher.py).
#
# Per ADR-0001 D2: tag operations mutate per-instance counters
# only. constitution_hash + DNA stay immutable; only what the
# agent KNOWS evolves.
#
# Verification:
#   PYTHONPATH=src:. pytest tests/unit/test_memory_tag_outcome.py
#                                tests/unit/test_procedural_shortcut_dispatch.py
#                                tests/unit/test_governance_pipeline.py
#                                tests/unit/test_tool_dispatcher.py
#                                tests/unit/test_procedural_shortcuts.py
#                                tests/unit/test_procedural_embedding.py
#   -> 215 passed
#
# Substrate ready for B183 (chat-tab thumbs UI). The frontend
# will:
#   - filter the conversation's audit events for
#     tool_call_shortcut entries (T4 made this a clean filter)
#   - render a thumbs-up / thumbs-down widget below the
#     assistant turn corresponding to the most recent shortcut
#     hit
#   - dispatch memory_tag_outcome.v1 with outcome=good|bad on
#     click, then update the widget state from the response
#
# Remaining ADR-0054 tranches:
#   T5b — chat-tab thumbs UI (B183)
#   T6 — settings UI panel + daemon-lifespan wiring + operator
#        safety guide

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/builtin/memory_tag_outcome.py \
        src/forest_soul_forge/tools/base.py \
        src/forest_soul_forge/tools/dispatcher.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_memory_tag_outcome.py \
        dev-tools/commit-bursts/commit-burst182-adr0054-t5-tag-outcome-tool.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0054 T5 — memory_tag_outcome.v1 (B182)

Burst 182. Tool side of T5 — operator-driven reinforcement of
procedural-shortcut hits. Chat-tab thumbs UI (the load-bearing
caller) follows in B183.

Ships:

memory_tag_outcome.v1: takes shortcut_id + outcome ∈
{good, bad, neutral}. good → strengthen +1, bad → weaken +1,
neutral → no counter change but still audit-visible.
Returns post-update counters + soft_deleted boolean
(True iff reinforcement_score < 0; row stays in the table
but search_by_cosine skips it). Refuses cleanly when
substrate unwired, shortcut_id unknown, or row belongs to a
different agent (cross-agent tagging is a privilege-
escalation surface — refused structurally).

side_effects=read_only because mutating per-instance
counters is the agent's own state per ADR-0001 D2 (identity
invariance). required_initiative_level=L2 because operator-
initiated by design — the agent never self-reinforces.

ToolContext gains procedural_shortcuts field; dispatcher's
GO leg + resume_approved leg both populate it from the
existing self.procedural_shortcuts_table. No new dispatcher
fields.

Catalog entry: memory_tag_outcome.v1 with full input_schema,
archetype_tags=[companion, assistant].

Tests: 21 unit tests across TestValidation, TestExecute,
TestToolMetadata. Real ProceduralShortcutsTable on a tmp
SQLite via Registry.bootstrap; FK-seeded agents 'i1' and
'i2' for the cross-agent refusal test.

Per ADR-0044 D3: zero kernel ABI changes. ToolContext gains
an optional field; existing tools + tests pass unchanged
(53/53 in test_tool_dispatcher.py).

Verification: 215 passed across the touched-modules sweep.

Remaining ADR-0054 tranches:
- T5b chat-tab thumbs UI (B183)
- T6 settings UI + daemon-lifespan wiring + safety guide"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 182 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
