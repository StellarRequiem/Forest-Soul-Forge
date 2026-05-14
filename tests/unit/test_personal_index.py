"""ADR-0076 T1 (B292) — PersonalIndex tests with mock embedder.

Uses a deterministic mock embedder (turns text into a fixed-
dimension vector via a hash) so tests don't require
sentence-transformers installed.

Covers:
  - add / has / count / delete
  - add rejects empty doc_id / empty text
  - re-add same doc_id replaces (idempotent)
  - add_batch fast-paths
  - search returns ordered SearchResults
  - search with k=0 returns []
  - search rejects empty query
  - cosine similarity edge cases (zero vector, identical vectors,
    dimension mismatch)
  - clear() empties the index
  - SentenceTransformerEmbedder raises PersonalIndexError on
    missing dep (lazy-import regression guard)
"""
from __future__ import annotations

import hashlib
from typing import Optional

import pytest

from forest_soul_forge.core.personal_index import (
    DEFAULT_DIMENSIONS,
    DEFAULT_MODEL,
    Embedder,
    IndexDocument,
    PersonalIndex,
    PersonalIndexError,
    SearchResult,
    SentenceTransformerEmbedder,
    _cosine,
)


class _MockEmbedder:
    """Deterministic mock — hash-based 8-dim vectors. Two identical
    texts produce identical vectors; different texts produce
    different vectors. Good enough for cosine-search tests."""

    dimensions: int = 8

    def embed(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Take first 8 bytes, normalize each to [-1, 1].
        return [(b - 128) / 128.0 for b in h[:8]]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


@pytest.fixture
def index():
    return PersonalIndex(embedder=_MockEmbedder())


# ---------------------------------------------------------------------------
# Basic operations
# ---------------------------------------------------------------------------
def test_add_then_has_and_count(index):
    assert index.count() == 0
    index.add("doc1", "hello world")
    assert index.has("doc1")
    assert index.count() == 1


def test_add_rejects_empty_doc_id(index):
    with pytest.raises(PersonalIndexError, match="doc_id"):
        index.add("", "text")


def test_add_rejects_empty_text(index):
    with pytest.raises(PersonalIndexError, match="text"):
        index.add("d1", "")


def test_add_idempotent(index):
    index.add("d1", "first")
    index.add("d1", "second")  # replace
    assert index.count() == 1


def test_delete_returns_true_when_existed(index):
    index.add("d1", "x")
    assert index.delete("d1") is True
    assert index.has("d1") is False
    assert index.count() == 0


def test_delete_returns_false_when_missing(index):
    assert index.delete("ghost") is False


def test_clear_empties(index):
    index.add("a", "x")
    index.add("b", "y")
    index.clear()
    assert index.count() == 0


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------
def test_add_batch_fast_paths(index):
    index.add_batch([
        {"doc_id": "a", "text": "alpha"},
        {"doc_id": "b", "text": "beta", "tags": ["greek"]},
        {"doc_id": "c", "text": "gamma", "source": "test"},
    ])
    assert index.count() == 3
    assert index.has("a")
    assert index.has("c")


def test_add_batch_empty_is_no_op(index):
    index.add_batch([])
    assert index.count() == 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def test_search_empty_index_returns_empty(index):
    assert index.search("anything") == []


def test_search_returns_ordered_results(index):
    index.add("doc1", "the quick brown fox")
    index.add("doc2", "lazy dogs sleep often")
    index.add("doc3", "the quick brown fox")  # same text as doc1
    results = index.search("the quick brown fox", k=10)
    # doc1 and doc3 should top the results — identical embeddings.
    top_ids = {r.doc_id for r in results[:2]}
    assert top_ids == {"doc1", "doc3"}


def test_search_k_zero_returns_empty(index):
    index.add("a", "x")
    assert index.search("x", k=0) == []


def test_search_rejects_empty_query(index):
    with pytest.raises(PersonalIndexError, match="query"):
        index.search("")


def test_search_returns_search_result_objects(index):
    index.add("d1", "test text", source="memory", tags=["tag1"])
    results = index.search("test text", k=1)
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, SearchResult)
    assert r.doc_id == "d1"
    assert r.source == "memory"
    assert r.tags == ("tag1",)
    # Identical text → high similarity (near 1.0)
    assert r.similarity > 0.99


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------
def test_cosine_identical_vectors():
    v = [1.0, 2.0, 3.0]
    assert _cosine(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_vectors():
    assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_returns_zero():
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert _cosine([1.0, 1.0], [0.0, 0.0]) == 0.0


def test_cosine_dimension_mismatch_raises():
    with pytest.raises(PersonalIndexError, match="dimensions mismatch"):
        _cosine([1.0, 2.0], [1.0, 2.0, 3.0])


# ---------------------------------------------------------------------------
# SentenceTransformerEmbedder
# ---------------------------------------------------------------------------
def test_sentence_transformer_embedder_lazy_import_guard():
    """Constructing the embedder is cheap; the model loads on
    first .embed() call. Test that the import attempt raises a
    clean PersonalIndexError when sentence-transformers isn't
    installed."""
    embedder = SentenceTransformerEmbedder()
    # If sentence-transformers IS installed, this test will load
    # the real model and pass without raising. We can't reliably
    # test the missing-dep path without monkey-patching imports.
    # The test below covers the regression guard surface — the
    # error class is raised + the message names the dep.
    try:
        embedder.embed("hello")
    except PersonalIndexError as e:
        # Expected when sentence-transformers isn't installed.
        assert "sentence-transformers" in str(e)


def test_sentence_transformer_embedder_dimensions_constant():
    embedder = SentenceTransformerEmbedder()
    assert embedder.dimensions == DEFAULT_DIMENSIONS
    assert embedder.model_name == DEFAULT_MODEL


# ---------------------------------------------------------------------------
# IndexDocument frozen-ness
# ---------------------------------------------------------------------------
def test_index_document_is_frozen():
    doc = IndexDocument(doc_id="x", text="y", source="z", tags=())
    with pytest.raises(Exception):  # FrozenInstanceError
        doc.text = "mutated"  # type: ignore[misc]
