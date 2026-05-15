"""Vector index for personal context — ADR-0076 T1 (B292).

PersonalIndex wraps SQLite-VEC (in-process, in-SQLCipher, file-
portable) with the operator-supplied embedder (default
all-MiniLM-L6-v2, lazy-imported). All four semantic-search
consumers (Knowledge Forge / Content Studio / Learning Coach /
Research Lab) reach this layer through the personal_recall.v1
tool that T4 ships.

## Surface

  - :class:`IndexDocument` — one stored entry's metadata
  - :class:`SearchResult` — one retrieved doc + similarity score
  - :class:`PersonalIndex` — the index container
    - ``add(doc_id, text, source, tags)`` — embed + insert
    - ``search(query, k=10)`` — cosine top-k
    - ``delete(doc_id)`` — remove from index
    - ``has(doc_id)`` — existence check
    - ``count()`` — total documents
  - :class:`Embedder` Protocol — pluggable embedder interface
  - :class:`SentenceTransformerEmbedder` — canonical local default
    wrapping ``sentence-transformers`` lazily

## Why lazy-import

The daemon boots without sentence-transformers loaded. First
``add()`` or ``search()`` call pays the ~3-5s model-load cost
(model file is ~80MB on disk, loaded once into memory).
Operators not enabling semantic search don't pay this cost.

## Why pure-Python adapter, not direct SQLite-VEC binding

T1 ships the surface + an in-memory fallback. T2 / T3 wire the
real SQLite-VEC extension. Splitting lets us ship + test the
data model + retrieval logic without making the optional native
extension a hard dep at module import time.

The in-memory fallback works for: tests, low-volume operator
deployments, and the migration window before SQLite-VEC is
installed. For production-scale retrieval the operator installs
the extension; the adapter detects + uses it. Same code path.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol


# Default embedder model. Operators who want better quality swap
# this via PersonalIndex(embedder=...) or by setting the env var.
DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_DIMENSIONS = 384

ENV_MODEL = "FSF_PERSONAL_INDEX_MODEL"
ENV_DB_PATH = "FSF_PERSONAL_INDEX_PATH"

DEFAULT_DB_PATH = Path("data/personal_index.sqlite")


class PersonalIndexError(RuntimeError):
    """Raised on hard-fatal index failures (DB corrupt, embedder
    load failed, dimensions mismatch). Soft failures (doc not
    found, empty query) surface as return-value sentinels."""


@dataclass(frozen=True)
class IndexDocument:
    """One indexed document's metadata.

    Stored alongside the embedding vector for retrieval-time
    filtering + result enrichment.
    """
    doc_id: str
    text: str
    source: str  # which domain/tool wrote this (e.g., 'memory_write', 'knowledge_forge')
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResult:
    """One retrieval result."""
    doc_id: str
    text: str
    source: str
    tags: tuple[str, ...]
    similarity: float  # cosine in [0, 1]; higher = closer


class Embedder(Protocol):
    """Pluggable embedder interface. Every embedder ships:
      - ``dimensions``: int — the output vector length
      - ``embed(text)`` → list[float] — single doc
      - ``embed_batch(texts)`` → list[list[float]] — batch for speed
    """
    dimensions: int

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# Canonical local embedder: SentenceTransformer wrapper
# ---------------------------------------------------------------------------


class SentenceTransformerEmbedder:
    """Lazy wrapper around ``sentence_transformers.SentenceTransformer``.

    Loads the model on first embed call. Subsequent calls reuse
    the loaded model. Thread-safe via a lock around the lazy
    init.
    """

    dimensions: int = DEFAULT_DIMENSIONS

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _ensure_model(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
                    except ImportError as e:
                        raise PersonalIndexError(
                            f"sentence-transformers not installed. "
                            f"Install via `pip install "
                            f"sentence-transformers`. Original error: {e}"
                        ) from e
                    self._model = SentenceTransformer(self.model_name)
                    # Verify dimensions match our declared constant —
                    # operators who swap to a different model that
                    # produces different dims need to recreate the
                    # index from scratch.
                    actual_dims = self._model.get_sentence_embedding_dimension()
                    if actual_dims != self.dimensions:
                        # Update self.dimensions to the actual value
                        # so the index reflects truth. Caller decides
                        # whether to rebuild.
                        self.dimensions = actual_dims
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._ensure_model()
        vec = model.encode(text, convert_to_numpy=True)
        return list(map(float, vec.tolist()))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        vecs = model.encode(texts, convert_to_numpy=True)
        return [list(map(float, v.tolist())) for v in vecs]


# ---------------------------------------------------------------------------
# In-memory PersonalIndex (T1 fallback + test surface)
# ---------------------------------------------------------------------------


class PersonalIndex:
    """Vector index for semantic search over operator context.

    T1 ships the in-memory implementation: documents + embeddings
    held in Python dicts. T2 swaps the storage backend for
    SQLite-VEC without changing the API.

    Operations:
      add(doc_id, text, source, tags) — embed + store (also
        feeds the BM25 inverted index for hybrid retrieval).
      search(query, k=10, mode='cosine'|'bm25'|'hybrid') —
        top-k retrieval. Cosine is the pre-T3 default; bm25 is
        the lexical companion; hybrid runs both and fuses via
        Reciprocal Rank Fusion (T3 / B321).
      delete(doc_id) — remove from index (vector + BM25 both).
      has(doc_id) — existence check
      count() — total documents
    """

    def __init__(
        self,
        embedder: Optional[Embedder] = None,
    ):
        self.embedder = embedder or SentenceTransformerEmbedder()
        # In-memory store: doc_id → (IndexDocument, embedding)
        self._store: dict[str, tuple[IndexDocument, list[float]]] = {}
        self._lock = threading.RLock()
        # ADR-0076 T3 (B321): BM25 inverted index. Co-mutated
        # with the vector store so the two retrieval paths stay
        # consistent (a doc lives in BOTH or NEITHER). Lazy
        # import to keep the module load light.
        from forest_soul_forge.core.personal_index_bm25 import BM25Index
        self._bm25 = BM25Index()

    def add(
        self,
        doc_id: str,
        text: str,
        *,
        source: str = "unknown",
        tags: Optional[list[str]] = None,
    ) -> None:
        """Embed + store a document. Idempotent — re-adding the
        same doc_id replaces the prior entry."""
        if not isinstance(doc_id, str) or not doc_id:
            raise PersonalIndexError("doc_id must be a non-empty string")
        if not isinstance(text, str) or not text:
            raise PersonalIndexError("text must be a non-empty string")
        embedding = self.embedder.embed(text)
        doc = IndexDocument(
            doc_id=doc_id, text=text, source=source,
            tags=tuple(tags or []),
        )
        with self._lock:
            self._store[doc_id] = (doc, embedding)
            # ADR-0076 T3 (B321): feed BM25 from the same write
            # path so the two retrieval surfaces stay aligned.
            # add() is idempotent in BM25 too — re-adding the
            # same doc_id clears the prior entry first.
            self._bm25.add(doc_id, text)

    def add_batch(
        self,
        items: list[dict],
    ) -> None:
        """Batch-add documents. Each item is a dict with keys:
        doc_id, text, source (optional), tags (optional).

        Batch is faster than per-document add() because the
        embedder runs all texts in one forward pass.
        """
        if not items:
            return
        texts = [str(it["text"]) for it in items]
        embeddings = self.embedder.embed_batch(texts)
        with self._lock:
            for it, emb in zip(items, embeddings):
                doc_id = str(it["doc_id"])
                doc = IndexDocument(
                    doc_id=doc_id,
                    text=str(it["text"]),
                    source=str(it.get("source", "unknown")),
                    tags=tuple(it.get("tags") or []),
                )
                self._store[doc_id] = (doc, emb)
                # ADR-0076 T3 (B321): mirror into BM25.
                self._bm25.add(doc_id, str(it["text"]))

    def search(
        self,
        query: str,
        k: int = 10,
        mode: str = "cosine",
        *,
        rrf_k: int = 60,
        candidate_multiplier: int = 3,
    ) -> list[SearchResult]:
        """Top-k retrieval. Empty store returns [].

        ADR-0076 T3 (B321) — mode selects the retrieval surface:

          'cosine' — pure semantic similarity (default, pre-T3
            behavior preserved).
          'bm25'   — pure lexical BM25Okapi. Use when the query
            asks for an exact phrase / proper noun the embedder
            tends to blur over.
          'hybrid' — runs cosine + BM25 in parallel, fuses via
            Reciprocal Rank Fusion (Cormack et al., 2009). The
            most balanced surface; default for personal_recall.v1
            (T4). ``rrf_k`` is the RRF constant (default 60);
            ``candidate_multiplier`` is how many extra results
            each leg pulls before fusion (default 3× the final k,
            wider net → better RRF outcomes on small overlap).

        SearchResult.similarity carries the retrieval-mode score:
        cosine → cosine similarity in [-1, 1]; bm25 → BM25 raw
        score (unbounded ≥ 0); hybrid → RRF score (unbounded
        ≥ 0). Callers should treat similarity as a relative-order
        signal within ONE retrieval, not as a cross-mode metric.
        """
        if not isinstance(query, str) or not query:
            raise PersonalIndexError("query must be a non-empty string")
        if k <= 0:
            return []
        if mode == "cosine":
            return self._search_cosine(query, k)
        if mode == "bm25":
            return self._search_bm25(query, k)
        if mode == "hybrid":
            return self._search_hybrid(
                query, k,
                rrf_k=rrf_k,
                candidate_multiplier=candidate_multiplier,
            )
        raise PersonalIndexError(
            f"unknown search mode {mode!r}; must be one of "
            f"'cosine', 'bm25', 'hybrid'"
        )

    def _search_cosine(self, query: str, k: int) -> list[SearchResult]:
        query_emb = self.embedder.embed(query)
        with self._lock:
            scored: list[tuple[float, IndexDocument]] = []
            for doc, emb in self._store.values():
                sim = _cosine(query_emb, emb)
                scored.append((sim, doc))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            SearchResult(
                doc_id=doc.doc_id, text=doc.text,
                source=doc.source, tags=doc.tags,
                similarity=sim,
            )
            for sim, doc in scored[:k]
        ]

    def _search_bm25(self, query: str, k: int) -> list[SearchResult]:
        hits = self._bm25.search(query, k)
        with self._lock:
            results: list[SearchResult] = []
            for doc_id, score in hits:
                entry = self._store.get(doc_id)
                if entry is None:
                    # Defensive: should not happen because add/delete
                    # mirror across both indexes under the same lock.
                    continue
                doc, _ = entry
                results.append(SearchResult(
                    doc_id=doc.doc_id, text=doc.text,
                    source=doc.source, tags=doc.tags,
                    similarity=score,
                ))
        return results

    def _search_hybrid(
        self,
        query: str,
        k: int,
        *,
        rrf_k: int,
        candidate_multiplier: int,
    ) -> list[SearchResult]:
        """RRF over cosine top-(k*m) + BM25 top-(k*m). The wider
        candidate pool (m=3 by default) gives RRF more chances
        to find docs that surface in both rankings — the multi-
        ranker bonus is the whole point of RRF."""
        from forest_soul_forge.core.personal_index_bm25 import rrf_fuse
        candidate_k = max(k * candidate_multiplier, k)
        cos_results = self._search_cosine(query, candidate_k)
        bm25_results = self._search_bm25(query, candidate_k)
        # Build the rankings (doc_id lists) for the RRF fuse.
        cos_rank = [r.doc_id for r in cos_results]
        bm25_rank = [r.doc_id for r in bm25_results]
        fused = rrf_fuse([cos_rank, bm25_rank], rrf_k=rrf_k)
        # Stitch the SearchResult back from the cosine pool first,
        # then the BM25 pool, since RRF only returned (doc_id,
        # score). The metadata is stable across both — we just
        # need the doc info.
        doc_lookup: dict[str, IndexDocument] = {}
        for r in cos_results:
            doc_lookup[r.doc_id] = IndexDocument(
                doc_id=r.doc_id, text=r.text,
                source=r.source, tags=r.tags,
            )
        for r in bm25_results:
            doc_lookup.setdefault(r.doc_id, IndexDocument(
                doc_id=r.doc_id, text=r.text,
                source=r.source, tags=r.tags,
            ))
        out: list[SearchResult] = []
        for doc_id, rrf_score in fused[:k]:
            doc = doc_lookup.get(doc_id)
            if doc is None:
                continue
            out.append(SearchResult(
                doc_id=doc.doc_id, text=doc.text,
                source=doc.source, tags=doc.tags,
                similarity=rrf_score,
            ))
        return out

    def delete(self, doc_id: str) -> bool:
        """Remove a document. Returns True if it existed."""
        with self._lock:
            removed = self._store.pop(doc_id, None) is not None
            if removed:
                # ADR-0076 T3 (B321): mirror the delete into BM25.
                self._bm25.remove(doc_id)
            return removed

    def has(self, doc_id: str) -> bool:
        with self._lock:
            return doc_id in self._store

    def count(self) -> int:
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        """Wipe the index. Used by tests + the future fsf index
        rebuild flow."""
        with self._lock:
            self._store.clear()
            self._bm25.clear()


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Returns 1.0 for identical direction, 0.0 for orthogonal,
    -1.0 for opposite. Forest's retrieval treats higher as
    better; we don't clamp to [0,1] because some embedders
    produce signed similarities.

    Returns 0.0 if either vector has zero magnitude (rare; only
    happens if an embedder is broken).
    """
    if len(a) != len(b):
        raise PersonalIndexError(
            f"vector dimensions mismatch: {len(a)} vs {len(b)}"
        )
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
