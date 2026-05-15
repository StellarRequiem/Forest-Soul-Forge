"""BM25 lexical retrieval for PersonalIndex — ADR-0076 T3 (B321).

BM25Okapi side of the hybrid recall path. Co-lives with the
vector index in PersonalIndex; the search() entry point fuses
both via Reciprocal Rank Fusion (RRF, Cormack et al. 2009) so
queries get both lexical-precision matches (the operator's exact
phrasing) and semantic-recall matches (paraphrases / synonyms).

## Why BM25 alongside cosine

Sentence-transformer embeddings shine at semantic similarity
("dog" ≈ "puppy") but blur lexical specificity ("Acme Corp" vs
"Acme Corporation"). Operators ask both kinds of question:
"remind me what I said about my back pain" (semantic) AND
"what's my routing number" (lexical). RRF gives us both without
having to tune a single similarity metric.

## RRF formula

For each candidate doc d, rrf_score(d) = sum over rankers r of
   1 / (rrf_k + rank_r(d))
where rank_r(d) is 1-indexed (top hit is rank=1) and rrf_k is a
small constant (default 60 per the Cormack paper). Docs that
appear in only one ranker contribute one term; docs in both
contribute two. The math intentionally favors docs that surface
in multiple rankers — that's the whole point.

## BM25 formula

The standard BM25Okapi scoring function:

   score(D, Q) = sum_{q in Q} idf(q) * (tf(q, D) * (k1 + 1)) /
                 (tf(q, D) + k1 * (1 - b + b * |D| / avgdl))

with k1 = 1.5 and b = 0.75 by Forest's convention. tf is
term-frequency in the doc; idf is inverse-document-frequency
log((N - df + 0.5) / (df + 0.5) + 1). |D| is the doc's token
length; avgdl is the corpus average.

## Tokenization

Lowercase ASCII split on non-alphanumeric. Tokens shorter than 2
chars or in the stop-word list are dropped. That's "good enough"
for the operator-context corpus we expect (English-dominant,
short notes); a future tranche may add language-aware
tokenization when we onboard non-English operators.
"""
from __future__ import annotations

import math
import re
import threading
from collections import defaultdict
from dataclasses import dataclass


# Small English stop-word list. Kept short on purpose: we want
# BM25 to weight rare terms heavily, so the IDF curve does most
# of the work. Removing only the truly noise-only function words
# helps short queries.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "is", "it", "its", "of", "on", "or", "that",
    "the", "to", "was", "were", "will", "with", "i", "you", "we",
    "they", "this", "those", "these", "but", "if", "then", "so",
    "do", "did", "does", "not", "no", "yes", "my", "your", "our",
    "their", "his", "her", "him", "she", "he", "them", "us", "me",
})

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


# BM25 hyperparameters. Conservative defaults that work well on
# short-doc corpora (notes / memory entries). Tuning these per
# operator is a future tranche concern.
DEFAULT_K1: float = 1.5
DEFAULT_B: float = 0.75
DEFAULT_RRF_K: int = 60


def tokenize(text: str) -> list[str]:
    """Lowercase + alphanumeric-split + stop-word drop + min-len 2.

    Returns the token list (with duplicates — BM25 wants term
    frequency, so we preserve them).
    """
    return [
        t for t in (m.group(0).lower() for m in _TOKEN_RE.finditer(text))
        if len(t) >= 2 and t not in STOPWORDS
    ]


@dataclass
class BM25Index:
    """In-memory BM25 inverted index. Co-mutated with PersonalIndex's
    vector store so add()/delete() stay consistent across both
    retrieval paths.

    All state-mutating methods grab ``self._lock`` (RLock) so
    concurrent add()s from different threads don't tear the
    inverted index. PersonalIndex's outer lock is the primary
    serialization point in production; this one is belt+
    suspenders so the module is safe in isolation.
    """

    k1: float = DEFAULT_K1
    b: float = DEFAULT_B

    def __post_init__(self) -> None:
        # term → {doc_id → term_frequency}. defaultdict of dict
        # rather than nested defaultdict so we can prune docs
        # cleanly on delete (drop empty inner dict explicitly).
        self._postings: dict[str, dict[str, int]] = defaultdict(dict)
        # doc_id → token length (count, NOT unique terms). Used
        # for the BM25 length-normalization term.
        self._doc_lengths: dict[str, int] = {}
        # Running corpus-length sum for incremental avgdl.
        self._total_length: int = 0
        self._lock = threading.RLock()

    # ---- mutation ------------------------------------------------------

    def add(self, doc_id: str, text: str) -> None:
        """Tokenize + insert. Idempotent: a re-add for the same
        doc_id removes the prior entry first so its term counts
        don't double-count."""
        tokens = tokenize(text)
        with self._lock:
            if doc_id in self._doc_lengths:
                self._remove_locked(doc_id)
            # Build the per-doc term-frequency table.
            tf: dict[str, int] = defaultdict(int)
            for tok in tokens:
                tf[tok] += 1
            for tok, count in tf.items():
                self._postings[tok][doc_id] = count
            self._doc_lengths[doc_id] = len(tokens)
            self._total_length += len(tokens)

    def remove(self, doc_id: str) -> bool:
        with self._lock:
            return self._remove_locked(doc_id)

    def _remove_locked(self, doc_id: str) -> bool:
        if doc_id not in self._doc_lengths:
            return False
        # Walk the postings list lazily — for each term we know
        # the doc was in, drop it. We can't iterate over all
        # postings (that'd be O(vocab)); instead, for every term
        # that appears in the doc, hit just that posting list.
        # We don't have an inverted-by-doc index, so we
        # re-tokenize from… wait, we threw away the text. We
        # need to walk all posting lists. Acceptable: delete is
        # rare; vocab is small.
        empty_terms: list[str] = []
        for term, doc_to_tf in self._postings.items():
            if doc_id in doc_to_tf:
                del doc_to_tf[doc_id]
                if not doc_to_tf:
                    empty_terms.append(term)
        for term in empty_terms:
            del self._postings[term]
        self._total_length -= self._doc_lengths[doc_id]
        del self._doc_lengths[doc_id]
        return True

    def clear(self) -> None:
        with self._lock:
            self._postings.clear()
            self._doc_lengths.clear()
            self._total_length = 0

    # ---- retrieval -----------------------------------------------------

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        """Top-k BM25 over the inverted index. Empty corpus or
        empty query returns []."""
        if k <= 0:
            return []
        with self._lock:
            if not self._doc_lengths:
                return []
            terms = tokenize(query)
            if not terms:
                return []
            n = len(self._doc_lengths)
            avgdl = self._total_length / n
            scores: dict[str, float] = defaultdict(float)
            for term in terms:
                if term not in self._postings:
                    continue
                postings = self._postings[term]
                df = len(postings)
                # BM25's "plus 1" idf variant (always >= 0).
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
                for doc_id, tf in postings.items():
                    dl = self._doc_lengths[doc_id]
                    norm = 1.0 - self.b + self.b * (dl / avgdl)
                    score = idf * (tf * (self.k1 + 1.0)) / (
                        tf + self.k1 * norm
                    )
                    scores[doc_id] += score
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            return ranked[:k]

    # ---- introspection -------------------------------------------------

    def count(self) -> int:
        with self._lock:
            return len(self._doc_lengths)

    def has(self, doc_id: str) -> bool:
        with self._lock:
            return doc_id in self._doc_lengths


def rrf_fuse(
    rankings: list[list[str]],
    *,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over N rankings.

    Each entry in ``rankings`` is a list of doc_ids ordered
    best-first (rank 1 first). Returns a unified ranking
    [(doc_id, rrf_score)] sorted by score descending.

    A doc appearing in multiple rankings sums its contributions.
    A doc appearing in only one ranking gets one term. Docs not
    in any ranking are absent from the output.

    Reference: Cormack, Clarke, Buettcher (2009),
    "Reciprocal Rank Fusion outperforms Condorcet and individual
    Rank Learning Methods."
    """
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for idx, doc_id in enumerate(ranking):
            rank = idx + 1  # 1-indexed
            scores[doc_id] += 1.0 / (rrf_k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
