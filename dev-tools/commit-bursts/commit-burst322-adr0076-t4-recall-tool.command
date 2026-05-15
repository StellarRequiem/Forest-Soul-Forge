#!/bin/bash
# Burst 322 - ADR-0076 T4: personal_recall.v1 tool.
#
# Read surface for the operator's PersonalIndex. Wraps the
# hybrid BM25+cosine RRF retrieval (T3) and gates by genre so
# only operator-context agents (companion / assistant /
# operator_steward / domain_orchestrator) can read. The four
# ten-domain consumers (Knowledge Forge / Content Studio /
# Learning Coach / Research Lab) reach for this.
#
# What ships:
#
# 1. src/forest_soul_forge/tools/builtin/personal_recall.py (NEW):
#    - PersonalRecallTool: name=personal_recall, version='1',
#      side_effects=read_only, requires_human_approval=False.
#    - validate(): query non-empty str, limit 1..50, mode in
#      {hybrid, cosine, bm25}.
#    - execute(): genre gate (refuses with not-authorized for
#      genres outside PERSONAL_SCOPE_ALLOWED_GENRES), substrate
#      gate (refuses with substrate_unwired when ctx.personal_index
#      is None), then index.search() with the parsed mode.
#    - Output: {count, mode, hits: [{doc_id, text, source, tags,
#      similarity}, ...]}.
#    - audit_payload records query_hash NOT raw query — operator
#      privacy first; the chain never carries what the operator
#      searched for in plaintext.
#
# 2. src/forest_soul_forge/tools/base.py:
#    - ToolContext gains personal_index: Any = None. Set by
#      dispatcher → ctx threading.
#
# 3. src/forest_soul_forge/tools/dispatcher.py:
#    - ToolDispatcher gains personal_index field; both
#      ToolContext build sites set ctx.personal_index from it.
#
# 4. src/forest_soul_forge/daemon/deps.py:
#    - Passes app.state.personal_index into the dispatcher.
#
# 5. src/forest_soul_forge/tools/builtin/__init__.py:
#    - Registers PersonalRecallTool.
#
# 6. config/tool_catalog.yaml:
#    - personal_recall.v1 entry with side_effects=read_only,
#      input_schema, archetype_tags = the four allowed genres.
#
# Tests (test_personal_recall_tool.py - 24 cases):
#   validate (8):
#     missing/empty/non-string query, limit bounds, unknown mode,
#     all valid modes, default mode implicit
#   execute gating (6):
#     unauthorized genre refused, no-index refused, all 4 allowed
#     genres can recall (parametrized)
#   execute delegation (5):
#     default mode hybrid + default limit 10, cosine forwarded,
#     bm25 forwarded, hits include full metadata, side_effect_
#     summary mentions mode + limit + count
#   privacy (1):
#     audit_payload records query_hash NOT raw query
#   error wrapping (1):
#     retrieval RuntimeError wrapped as ToolError
#   static config (3):
#     tool metadata, _hash_query stable + short, hashes differ
#
# Sandbox-verified 24/24 pass.
#
# === ADR-0076 progress: 4/6 tranches closed ===
# Next: T5 fsf index rebuild CLI, T6 runbook.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/builtin/personal_recall.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        src/forest_soul_forge/tools/base.py \
        src/forest_soul_forge/tools/dispatcher.py \
        src/forest_soul_forge/daemon/deps.py \
        config/tool_catalog.yaml \
        tests/unit/test_personal_recall_tool.py \
        dev-tools/commit-bursts/commit-burst322-adr0076-t4-recall-tool.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0076 T4 - personal_recall.v1 tool (B322)

Burst 322. Read surface for the operator's PersonalIndex. Wraps
the hybrid BM25+cosine RRF retrieval (T3) and gates by genre so
only operator-context agents (companion / assistant /
operator_steward / domain_orchestrator) can read. The four ten-
domain consumers (Knowledge Forge / Content Studio / Learning
Coach / Research Lab) reach for this.

What ships:

  - tools/builtin/personal_recall.py (NEW): PersonalRecallTool
    with side_effects=read_only + requires_human_approval=False.
    validate() enforces query non-empty + limit 1..50 + mode in
    {hybrid, cosine, bm25}. execute() applies genre gate
    (PERSONAL_SCOPE_ALLOWED_GENRES) then substrate gate
    (ctx.personal_index None → refuses substrate_unwired) then
    delegates to index.search().

  - tools/base.py: ToolContext gains personal_index field.

  - tools/dispatcher.py: ToolDispatcher gains personal_index;
    both ToolContext build sites set it.

  - daemon/deps.py: passes app.state.personal_index into the
    dispatcher.

  - tools/builtin/__init__.py: registers PersonalRecallTool.

  - config/tool_catalog.yaml: personal_recall.v1 entry, four
    allowed-genre archetype_tags.

Operator-privacy invariant: audit_payload records query_hash
(SHA-256 truncated 16 chars) NOT the raw query — the chain
never carries what the operator searched for in plaintext.

Tests: test_personal_recall_tool.py — 24 cases covering 8
validate, 6 execute gating (including parametrized over all 4
allowed genres), 5 execute delegation, 1 privacy invariant, 1
error wrapping, 3 static config. Sandbox-verified 24/24 pass.

ADR-0076 progress: 4/6 tranches closed (T1 substrate + T2
indexer + T3 hybrid RRF + T4 read tool). Next: T5 fsf index
rebuild CLI, T6 runbook."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 322 complete - ADR-0076 T4 personal_recall.v1 shipped ==="
echo "ADR-0076: 4/6 tranches closed."
echo ""
echo "Press any key to close."
read -n 1
