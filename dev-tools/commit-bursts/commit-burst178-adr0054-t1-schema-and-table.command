#!/bin/bash
# Burst 178 — ADR-0054 T1 — schema v15 to v16 + procedural-shortcut
# table accessor.
#
# First implementation tranche of ADR-0054. Lands the substrate
# the dispatcher's ProceduralShortcutStep (T3) and reinforcement
# tools (T5) will sit on. Per ADR-0054 D1 + D2, the new table
# stores per-instance situation->action shortcuts with cosine-
# searchable embeddings + reinforcement counters.
#
# Per ADR-0001 D2 identity invariance: this table holds per-
# instance STATE, not identity. constitution_hash + DNA stay
# immutable; only what the agent KNOWS evolves. Operators can
# rebuild this table freely without touching the agent's
# identity.
#
# What ships:
#
#   src/forest_soul_forge/registry/schema.py:
#     - SCHEMA_VERSION bumped 15 to 16
#     - DDL_STATEMENTS gets a new memory_procedural_shortcuts
#       table (fresh-install path)
#     - MIGRATIONS[16] adds the same table for v15 to v16 upgrades
#       (with explanatory comment cross-referencing ADR-0001 D2
#       and ADR-0054)
#     - Indexes on instance_id for fast per-agent lookups
#     - CHECK constraints on action_kind ('response', 'tool_call',
#       'no_op') and learned_from_kind ('auto', 'operator_tagged')
#
#   src/forest_soul_forge/registry/tables/procedural_shortcuts.py:
#     ProceduralShortcutsTable with:
#       - put: insert a new shortcut with float32 embedding BLOB
#       - get: fetch by id; KeyError on absent
#       - strengthen / weaken: increment success/failure counts
#       - record_match: update last_matched_at + last_matched_seq
#         when ProceduralShortcutStep selects the row
#       - delete: hard-delete; idempotent on absent
#       - list_by_instance: default excludes soft-deleted
#         (failure > success); include_negative=True for forensics
#       - search_by_cosine: per ADR-0054 D2 — two-stage filter
#         (cosine >= floor AND reinforcement >= floor); brute-
#         force NumPy scan; combined-score ranking
#         (cosine + 0.05 log(success+1)); skips mixed-dimension
#         rows rather than crashing
#       - count_by_instance: simple COUNT
#
#     Plus ProceduralShortcut frozen dataclass (with .reinforcement_score
#     property) and _encode_embedding / _decode_embedding /
#     _normalize helpers for stable float32 little-endian BLOB
#     storage.
#
#   tests/unit/test_procedural_shortcuts.py:
#     25 unit tests covering:
#       Schema (5 tests):
#         - SCHEMA_VERSION == 16
#         - fresh install creates the table
#         - expected columns present
#         - action_kind CHECK constraint enforced at SQL level
#         - learned_from_kind CHECK constraint enforced
#         - FK to agents enforced (PRAGMA foreign_keys=ON)
#
#       Embedding (5 tests):
#         - encode/decode round-trip is byte-stable
#         - rejects 2-D arrays
#         - rejects integer arrays
#         - normalize handles zero-vector safely
#         - normalize produces unit vector
#
#       CRUD (8 tests):
#         - put then get round-trip
#         - get unknown raises KeyError
#         - put rejects bad action_kind
#         - strengthen/weaken/record_match update fields correctly
#         - strengthen/weaken reject zero/negative
#         - delete removes; idempotent on absent
#         - list_by_instance excludes soft-deleted by default;
#           include_negative=True surfaces them
#         - count_by_instance returns per-agent count
#
#       Search (6 tests):
#         - search returns match above threshold
#         - search excludes below cosine floor
#         - search excludes below reinforcement floor (default 2)
#         - top_k ranks by combined score (cosine + 0.05 log(N+1))
#         - mixed embedding dimensions silently skipped
#         - invalid cosine_floor / top_k raise ValueError
#
#       Migration (1 test):
#         - v15 to v16 upgrade creates the table on existing DBs
#
#     Plus updates to tests/unit/test_registry.py: 6 assertions
#     bumped from r.schema_version() == 15 to == 16 to track the
#     migration.
#
# Per ADR-0044 D3: schema migrations are part of the userspace
# contract; additive migrations (new tables, new columns with
# safe defaults) are non-breaking. Pre-v16 daemons obviously
# don't have this table; v16 daemons reading pre-v16 audit chains
# replay the chain cleanly because no existing event types
# reference it.
#
# Verification:
#   PYTHONPATH=src:. pytest tests/unit/test_procedural_shortcuts.py
#                                tests/unit/test_registry.py
#                                tests/unit/test_registry_concurrency.py
#                                + the touched-modules sweep
#   -> 281 passed, 4 macOS-only skips, 1 xfail (the documented
#      pre-existing v6 to v7 SQLite-version test-setup oddity)
#
# Remaining ADR-0054 tranches (queued):
#   T2 — embedding adapter (nomic-embed-text wired into the path)
#   T3 — ProceduralShortcutStep + StepResult.shortcut verdict
#   T4 — audit emission (tool_call_shortcut event type)
#   T5 — reinforcement tools (memory_tag_outcome.v1 + chat-tab
#        thumbs surface)
#   T6 — settings UI + operator safety guide

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/registry/schema.py \
        src/forest_soul_forge/registry/tables/procedural_shortcuts.py \
        tests/unit/test_procedural_shortcuts.py \
        tests/unit/test_registry.py \
        dev-tools/commit-bursts/commit-burst178-adr0054-t1-schema-and-table.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0054 T1 — procedural-shortcut substrate (B178)

Burst 178. First implementation tranche of ADR-0054. Lands the
schema (v15 to v16) + table accessor that the dispatcher's
ProceduralShortcutStep (T3) and reinforcement tools (T5) will
sit on.

Per ADR-0001 D2 identity invariance: this table holds per-
instance STATE, not identity. constitution_hash + DNA stay
immutable; only what the agent KNOWS evolves.

Ships:
- schema.py: SCHEMA_VERSION 15 to 16, new
  memory_procedural_shortcuts table in DDL_STATEMENTS +
  MIGRATIONS[16]. CHECK constraints on action_kind and
  learned_from_kind. FK to agents.
- tables/procedural_shortcuts.py: ProceduralShortcutsTable with
  put/get/strengthen/weaken/record_match/delete/list_by_instance/
  search_by_cosine/count_by_instance. Float32 little-endian BLOB
  embedding storage. NumPy brute-force cosine + reinforcement
  gate per ADR-0054 D2 (cosine >= 0.92 AND success-failure >= 2
  default floors). Combined-score ranking
  (cosine + 0.05 log(success+1)) for top_k tiebreaks.

Tests: 25 unit tests covering schema parity (DDL vs MIGRATIONS),
CHECK constraints, FK enforcement, embedding round-trip, all
CRUD ops, search edge cases (below threshold, below
reinforcement, mixed dimensions, top_k ranking), v15 to v16
upgrade path. Plus 6 assertion bumps in test_registry.py to
track the schema version.

Per ADR-0044 D3: additive schema migration; pre-v16 daemons
unaffected; v16 daemons reading pre-v16 audit chains replay
cleanly because no existing event types reference the new
table.

Verification: 281 passed across the touched-modules sweep, 4
macOS-only skips, 1 documented pre-existing xfail.

Remaining ADR-0054 tranches:
- T2 embedding adapter (nomic-embed-text wired in)
- T3 ProceduralShortcutStep + StepResult.shortcut verdict
- T4 audit emission (tool_call_shortcut event type)
- T5 reinforcement tools (memory_tag_outcome.v1)
- T6 settings UI + operator safety guide"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 178 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
