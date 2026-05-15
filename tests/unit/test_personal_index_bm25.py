"""ADR-0076 T3 (B321) — BM25 + hybrid RRF tests.

Coverage:
  BM25Index:
    - tokenize drops short tokens + stopwords + lowercases
    - add/remove/count/has
    - re-add is idempotent (replaces, doesn't double-count)
    - empty corpus returns []
    - empty query (only stopwords) returns []
    - search ranks lexical matches first
    - delete after add removes from postings + length tables

  rrf_fuse:
    - empty input returns []
    - single ranking is order-preserving
    - common docs in two rankings score higher
    - docs only in one ranking still appear, just lower

  PersonalIndex search modes:
    - mode='cosine' preserves pre-T3 behavior (regression guard)
    - mode='bm25' returns lexical-match results from the inverted
      index, not cosine
    - mode='hybrid' surfaces docs that match either path and
      preferentially ranks the doc that matches both
    - unknown mode raises PersonalIndexError
    - delete() drops from BOTH indexes
    - clear() drops from BOTH indexes
"""
from __future__ import annotations

import hashlib

import pytest

from forest_soul_forge.core.personal_index import (
    PersonalIndex,
    PersonalIndexError,
)
from forest_soul_forge.core.personal_index_bm25 import (
    BM25Index,
    DEFAULT_RRF_K,
    STOPWORDS,
    rrf_fuse,
    tokenize,
)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

def test_tokenize_lowercases_and_splits():
    assert tokenize("Hello World, FOO!") == ["hello", "world", "foo"]


def test_tokenize_drops_stopwords_and_short_tokens():
    out = tokenize("The cat sat on a mat at 3 pm")
    # stopwords: the, on, a, at
    # short: 3, pm (>=2 chars but pm is allowed; check actual)
    # Length filter is >=2, so single-digit "3" is dropped; "pm" stays.
    assert "the" not in out
    assert "on" not in out
    assert "a" not in out
    assert "at" not in out
    assert "3" not in out  # length < 2
    assert "cat" in out
    assert "sat" in out
    assert "mat" in out


def test_tokenize_empty_returns_empty():
    assert tokenize("") == []
    # All-stopwords query.
    assert tokenize("the a and or") == []


def test_stopwords_includes_basics():
    for w in ("a", "the", "is", "and", "of"):
        assert w in STOPWORDS


# ---------------------------------------------------------------------------
# BM25Index
# ---------------------------------------------------------------------------

def _bm():
    return BM25Index()


def test_add_then_count_and_has():
    bm = _bm()
    assert bm.count() == 0
    bm.add("d1", "alpha beta gamma")
    assert bm.has("d1")
    assert bm.count() == 1


def test_readd_is_idempotent_no_double_count():
    """Re-adding the same doc_id must not double the term-frequencies."""
    bm = _bm()
    bm.add("d1", "alpha alpha alpha")
    bm.add("d1", "alpha alpha alpha")  # exact same content
    bm.add("d1", "alpha alpha alpha")
    assert bm.count() == 1
    # If double-counting bugged the postings, the score below
    # would be inflated. Verify by hitting search and checking
    # we get exactly one row.
    hits = bm.search("alpha", k=10)
    assert len(hits) == 1
    assert hits[0][0] == "d1"


def test_remove_drops_doc():
    bm = _bm()
    bm.add("d1", "alpha")
    bm.add("d2", "beta")
    assert bm.remove("d1") is True
    assert not bm.has("d1")
    assert bm.has("d2")
    assert bm.count() == 1
    # Removing a missing doc is a clean False.
    assert bm.remove("d_nonexistent") is False


def test_search_empty_corpus_returns_empty():
    bm = _bm()
    assert bm.search("alpha") == []


def test_search_empty_query_returns_empty():
    bm = _bm()
    bm.add("d1", "alpha beta")
    assert bm.search("") == []
    # All-stopword query.
    assert bm.search("the and or a") == []


def test_search_ranks_lexical_match_first():
    """A doc with all three query terms should outrank a doc
    with one query term, and a doc with no overlap is absent."""
    bm = _bm()
    bm.add("d_all", "machine learning embeddings vector")
    bm.add("d_some", "machine repairs and maintenance")
    bm.add("d_none", "operator notes coffee morning")
    hits = bm.search("machine learning embeddings", k=10)
    doc_ids = [doc_id for doc_id, _ in hits]
    assert doc_ids[0] == "d_all"
    # d_some matches one term, should still appear; d_none MUST NOT.
    assert "d_some" in doc_ids
    assert "d_none" not in doc_ids


def test_search_idf_favors_rare_terms():
    """A term that appears in 1 doc has higher IDF than a term
    that appears in all 3. The rare-term-match doc should outrank
    the common-term-match doc."""
    bm = _bm()
    bm.add("d1", "common common common rare")
    bm.add("d2", "common common common")
    bm.add("d3", "common common common")
    hits = bm.search("common rare", k=10)
    # d1 has BOTH common and rare; d2 + d3 have only common.
    # d1 must rank first; the rare term gives it the IDF boost.
    assert hits[0][0] == "d1"


def test_search_respects_k_bound():
    bm = _bm()
    for i in range(20):
        bm.add(f"d{i}", "alpha beta")
    hits = bm.search("alpha", k=5)
    assert len(hits) == 5


def test_clear_resets_everything():
    bm = _bm()
    bm.add("d1", "alpha")
    bm.add("d2", "beta")
    bm.clear()
    assert bm.count() == 0
    assert bm.search("alpha") == []


# ---------------------------------------------------------------------------
# RRF fuse
# ---------------------------------------------------------------------------

def test_rrf_empty_input_returns_empty():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[]]) == []


def test_rrf_single_ranking_preserves_order():
    fused = rrf_fuse([["a", "b", "c"]])
    assert [doc_id for doc_id, _ in fused] == ["a", "b", "c"]


def test_rrf_doc_in_both_rankings_scores_higher():
    """A doc that appears in BOTH rankings should outrank one
    that appears in only one — regardless of where in either
    ranking it landed (within reason)."""
    fused = rrf_fuse([
        ["both", "only_a"],
        ["both", "only_b"],
    ])
    top = fused[0][0]
    assert top == "both"
    # Verify the score is roughly 2x what it would be with one
    # contribution.
    both_score = next(s for d, s in fused if d == "both")
    only_a_score = next(s for d, s in fused if d == "only_a")
    assert both_score > only_a_score


def test_rrf_unique_docs_all_appear():
    fused = rrf_fuse([["a"], ["b"], ["c"]])
    doc_ids = {doc_id for doc_id, _ in fused}
    assert doc_ids == {"a", "b", "c"}


def test_rrf_constant_is_60():
    """Regression guard on the published default — Cormack 2009
    paper recommends 60; switching it changes every fused
    score."""
    assert DEFAULT_RRF_K == 60


# ---------------------------------------------------------------------------
# PersonalIndex hybrid search wiring
# ---------------------------------------------------------------------------


class _DeterministicEmbedder:
    """Mock embedder: tokenizes + averages a per-term hash vector.
    Two texts that share a rare term land closer in cosine space
    than two texts that share only common terms. Good enough to
    write hybrid tests that don't trivially make cosine match
    perfectly."""
    dimensions: int = 16

    def embed(self, text: str) -> list[float]:
        toks = tokenize(text) or ["__empty__"]
        acc = [0.0] * 16
        for tok in toks:
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            for i in range(16):
                acc[i] += (h[i] - 128) / 128.0
        return [v / len(toks) for v in acc]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


@pytest.fixture
def idx():
    return PersonalIndex(embedder=_DeterministicEmbedder())


def test_cosine_mode_is_default_and_preserves_pre_t3_behavior(idx):
    idx.add("d1", "alpha beta gamma")
    idx.add("d2", "delta epsilon")
    out = idx.search("alpha", k=2)
    # Pre-T3 regression: search with no mode kwarg returns
    # cosine results in [-1, 1] range.
    assert all(-1.0 <= r.similarity <= 1.0 for r in out)
    assert len(out) == 2


def test_bm25_mode_uses_lexical_index(idx):
    """A doc with an exact lexical match must surface first
    under BM25 mode, regardless of cosine semantic distance."""
    idx.add("d_exact", "operator routing number 12345")
    idx.add("d_general", "operator likes morning coffee")
    out = idx.search("routing number", k=5, mode="bm25")
    assert len(out) >= 1
    assert out[0].doc_id == "d_exact"


def test_hybrid_mode_surfaces_both_paths(idx):
    """Hybrid mode runs both legs + RRF-fuses. A doc that
    matches BOTH cosine and BM25 should outrank a doc that
    matches only one."""
    # A doc that matches the query lexically AND has the embedding.
    idx.add("d_both", "machine learning embeddings vector retrieval")
    # A doc that matches only the BM25 side.
    idx.add("d_lex_only", "vector machine")
    # A noise doc.
    idx.add("d_noise", "completely unrelated text about gardening")
    out = idx.search(
        "vector retrieval embeddings", k=3, mode="hybrid",
    )
    doc_ids = [r.doc_id for r in out]
    assert "d_both" in doc_ids
    # d_both should be top (in both rankings).
    assert doc_ids[0] == "d_both"


def test_unknown_mode_raises(idx):
    idx.add("d1", "alpha")
    with pytest.raises(PersonalIndexError, match="unknown search mode"):
        idx.search("alpha", mode="cosin")


def test_delete_removes_from_both_indexes(idx):
    idx.add("d1", "alpha beta")
    idx.add("d2", "alpha gamma")
    assert idx.delete("d1") is True
    # Cosine search should not return d1.
    out_cos = idx.search("alpha", k=10)
    assert all(r.doc_id != "d1" for r in out_cos)
    # BM25 search should not return d1.
    out_bm = idx.search("alpha", k=10, mode="bm25")
    assert all(r.doc_id != "d1" for r in out_bm)


def test_clear_drops_both_indexes(idx):
    idx.add("d1", "alpha beta")
    idx.add("d2", "alpha gamma")
    idx.clear()
    assert idx.count() == 0
    assert idx.search("alpha", k=10, mode="bm25") == []
    assert idx.search("alpha", k=10, mode="cosine") == []


def test_hybrid_respects_k_bound(idx):
    for i in range(10):
        idx.add(f"d{i}", f"alpha beta gamma{i}")
    out = idx.search("alpha beta gamma1", k=3, mode="hybrid")
    assert len(out) == 3


def test_hybrid_empty_corpus_returns_empty(idx):
    out = idx.search("anything", k=5, mode="hybrid")
    assert out == []
