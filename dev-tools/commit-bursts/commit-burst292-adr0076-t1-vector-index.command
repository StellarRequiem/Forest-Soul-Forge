#!/bin/bash
# Burst 292 — ADR-0076 T1: vector index substrate.
#
# Scale substrate. Semantic search across operator context for
# four ten-domain consumers: D1 Knowledge Forge, D7 Content
# Studio, D9 Learning Coach, D10 Research Lab. The orchestrator
# (ADR-0067 T2) also benefits when LLM JSON output is unreliable.
#
# What ships:
#
# 1. docs/decisions/ADR-0076-vector-index.md — full record.
#    Four decisions:
#      D1 SQLite-VEC as the store (in-process, in-SQLCipher,
#         file-portable; encryption-at-rest covers it automatically)
#      D2 all-MiniLM-L6-v2 as the canonical embedder (384 dims,
#         ~80MB, good local quality + size tradeoff for M-series)
#      D3 Hybrid BM25 + cosine retrieval via reciprocal-rank-fusion
#      D4 Index updates async on memory writes; rebuild via
#         operator CLI
#    Six tranches T1-T6.
#
# 2. src/forest_soul_forge/core/personal_index.py:
#    - Embedder Protocol (dimensions + embed + embed_batch)
#    - SentenceTransformerEmbedder canonical local default with
#      lazy import (daemon boots without sentence-transformers;
#      first embed pays ~3-5s model-load cost)
#    - IndexDocument frozen dataclass (doc_id + text + source + tags)
#    - SearchResult frozen dataclass (with similarity score)
#    - PersonalIndex container with thread-safe in-memory store:
#        add(doc_id, text, source=, tags=)
#        add_batch([items]) — fast-path for bulk embedding
#        search(query, k=10) — cosine top-k with descending order
#        delete(doc_id) → bool
#        has(doc_id) → bool
#        count() → int
#        clear() — wipe (used by tests + future fsf index rebuild)
#    - _cosine helper handling zero vectors + dimension-mismatch
#    - In-memory T1 storage; T2 swaps to SQLite-VEC without API
#      changes
#
# Tests (test_personal_index.py — 19 cases):
#   With deterministic mock embedder (hash-based 8-dim vectors):
#     - add/has/count basic ops
#     - reject empty doc_id + empty text
#     - idempotent re-add (replace)
#     - delete returns True/False on existed/missing
#     - clear empties
#     - add_batch fast path + empty no-op
#     - search empty → []
#     - search returns ordered SearchResult objects
#     - identical text → similarity > 0.99
#     - search k=0 → []
#     - search rejects empty query
#   Cosine helper:
#     - identical / orthogonal / opposite vectors
#     - zero-magnitude vector returns 0.0
#     - dimension mismatch raises PersonalIndexError
#   SentenceTransformerEmbedder:
#     - lazy-import guard (raises PersonalIndexError on missing dep)
#     - dimensions + model_name constants
#   IndexDocument is frozen.
#
# What's NOT in T1 (queued):
#   T2: Background indexer worker (memory_write → enqueue embed)
#   T3: Hybrid BM25 + cosine with RRF fusion
#   T4: personal_recall.v1 builtin tool — cross-domain surface
#   T5: fsf index rebuild CLI + /index/status endpoint
#   T6: Operator runbook + scaling characterization

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0076-vector-index.md \
        src/forest_soul_forge/core/personal_index.py \
        tests/unit/test_personal_index.py \
        dev-tools/commit-bursts/commit-burst292-adr0076-t1-vector-index.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(scale): ADR-0076 T1 — vector index substrate (B292)

Burst 292. Scale substrate. Semantic search for the four
domains (Knowledge Forge / Content Studio / Learning Coach /
Research Lab) + the orchestrator's intent-classification
fallback path.

What ships:

  - ADR-0076 full record. Four decisions: SQLite-VEC store
    (in-process, in-SQLCipher), all-MiniLM-L6-v2 embedder
    (384 dims, ~80MB, local), hybrid BM25 + cosine retrieval
    with RRF fusion, async index updates on memory writes.
    Six tranches T1-T6.

  - core/personal_index.py: Embedder Protocol +
    SentenceTransformerEmbedder canonical local default
    (lazy-imports sentence-transformers so daemon boot
    stays cheap; first embed pays ~3-5s model-load cost).
    IndexDocument + SearchResult frozen dataclasses.
    PersonalIndex container with thread-safe in-memory store:
    add / add_batch / search (cosine top-k) / delete / has /
    count / clear. _cosine handles zero-magnitude + dim
    mismatch defensively. T1 ships in-memory storage; T2
    swaps to SQLite-VEC without API changes.

Tests: test_personal_index.py — 19 cases with deterministic
mock embedder covering basic ops + batch fast path + search
ordering + edge cases (empty query, k=0, identical-text
similarity), cosine helper (identical/orthogonal/opposite/
zero/dim-mismatch), SentenceTransformerEmbedder lazy-import
guard, IndexDocument frozen-ness.

Queued T2-T6: background indexer worker, hybrid BM25+cosine
RRF, personal_recall.v1 tool, fsf index rebuild CLI +
/index/status endpoint, operator runbook."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 292 complete — ADR-0076 T1 vector index shipped ==="
echo "Next: T2 background indexer OR T4 personal_recall.v1 tool."
echo ""
echo "Press any key to close."
read -n 1
