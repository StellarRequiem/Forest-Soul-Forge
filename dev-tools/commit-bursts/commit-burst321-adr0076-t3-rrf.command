#!/bin/bash
# Burst 321 - ADR-0076 T3: hybrid BM25 + cosine via RRF.
#
# Adds the lexical retrieval companion to the vector index +
# fuses both rankings via Reciprocal Rank Fusion (Cormack 2009).
# personal_recall.v1 (T4) will default to mode='hybrid' so the
# operator gets both semantic recall AND exact-phrase precision.
#
# What ships:
#
# 1. src/forest_soul_forge/core/personal_index_bm25.py (NEW):
#    - tokenize() — lowercase ASCII split + stopword + min-len-2.
#    - BM25Index — in-memory inverted index. add/remove/search.
#      Idempotent re-add. BM25Okapi scoring with k1=1.5, b=0.75.
#      Standard IDF variant: log((N - df + 0.5)/(df + 0.5) + 1).
#    - rrf_fuse(rankings, rrf_k=60) — Reciprocal Rank Fusion
#      sum over rankers of 1/(rrf_k + rank). Default rrf_k=60.
#
# 2. src/forest_soul_forge/core/personal_index.py:
#    - PersonalIndex.__init__ also constructs a BM25Index.
#    - add() / add_batch() / delete() / clear() co-mutate the
#      BM25 index so the two surfaces stay aligned.
#    - search() gains mode kwarg: 'cosine' (default, pre-T3),
#      'bm25', 'hybrid'. Hybrid runs both legs + RRF-fuses,
#      defaulting candidate_multiplier=3 so RRF has a wider
#      pool of candidates and can find cross-ranker matches.
#
# Tests (test_personal_index_bm25.py - 27 cases):
#   tokenize (3): lowercase split, stopwords + short tokens,
#     empty/all-stopwords returns []
#   BM25Index (8): add/has/count, idempotent re-add, remove,
#     empty corpus, empty query, ranks matches first, IDF
#     favors rare terms, k bound, clear
#   rrf_fuse (5): empty in -> empty out, single ranking order,
#     both-rankings doc outranks one-ranking, all unique docs
#     surface, rrf_k=60 constant guard
#   PersonalIndex hybrid (8): cosine default preserves pre-T3,
#     bm25 returns lexical hits, hybrid surfaces both paths +
#     both-match outranks one-match, unknown mode raises,
#     delete drops both, clear drops both, k bound, empty corpus
#
# Sandbox-verified 57/57 tests pass across all PersonalIndex +
# MemoryIndexer suites.
#
# === ADR-0076 progress: 3/6 tranches closed (T1+T2+T3) ===
# Next: T4 personal_recall.v1 tool, T5 fsf index rebuild CLI,
# T6 runbook.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/personal_index_bm25.py \
        src/forest_soul_forge/core/personal_index.py \
        tests/unit/test_personal_index_bm25.py \
        dev-tools/commit-bursts/commit-burst321-adr0076-t3-rrf.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0076 T3 - hybrid BM25 + cosine RRF (B321)

Burst 321. Adds the lexical retrieval companion to the vector
index + fuses both rankings via Reciprocal Rank Fusion (Cormack
2009). personal_recall.v1 (T4) will default to mode='hybrid' so
the operator gets both semantic recall AND exact-phrase
precision.

What ships:

  - core/personal_index_bm25.py (NEW): BM25Index in-memory
    inverted index. tokenize() lowercases ASCII, splits on
    non-alphanumeric, drops stopwords + tokens shorter than 2.
    BM25Okapi scoring with k1=1.5 + b=0.75. rrf_fuse() sums
    1/(rrf_k + rank) per doc across N rankings (rrf_k default
    60 per the paper).

  - core/personal_index.py: PersonalIndex composes BM25Index
    alongside the vector store. add()/add_batch()/delete()/
    clear() co-mutate both surfaces so a doc lives in BOTH or
    NEITHER. search() gains mode kwarg (default 'cosine'
    preserves pre-T3 behavior). 'bm25' returns lexical-match
    results. 'hybrid' runs both legs with candidate_multiplier=3
    + RRF-fuses for the most balanced retrieval.

Tests: test_personal_index_bm25.py — 27 cases covering tokenize
(3), BM25Index (8), rrf_fuse (5), PersonalIndex hybrid wiring
(8) + assorted edge cases. Sandbox-verified 57/57 pass across
all PersonalIndex + MemoryIndexer suites.

ADR-0076 progress: 3/6 tranches closed (T1 substrate + T2
indexer + T3 hybrid RRF). Next: T4 personal_recall.v1 tool,
T5 fsf index rebuild CLI, T6 runbook."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 321 complete - ADR-0076 T3 hybrid RRF shipped ==="
echo "ADR-0076: 3/6 tranches closed."
echo ""
echo "Press any key to close."
read -n 1
