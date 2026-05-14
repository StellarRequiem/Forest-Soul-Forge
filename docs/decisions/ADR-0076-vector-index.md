# ADR-0076 — Vector Index for Personal Context

**Status:** Accepted (2026-05-14). Phase α scale substrate. The
load-bearing piece for semantic-search across operator memory +
knowledge artifacts + indexed conversation history.

## Context

Four of the ten domains need semantic similarity:

- **D1 Knowledge Forge** — "find related notes" / "what have I
  written that touches on X"
- **D7 Content Studio** — style matching against the operator's
  voice samples; topic cohesion checks
- **D9 Learning Coach** — concept similarity for spaced
  repetition + misconception clustering
- **D10 Research Lab** — citation-graph reasoning, source
  similarity

The cross-domain orchestrator (ADR-0067 T2) also benefits from
embedding-based intent classification when LLM JSON output is
unreliable.

What's available today: exact-match memory recall. No semantic
layer.

## Decision

This ADR locks **four** decisions:

### Decision 1 — SQLite-VEC for the index

Vector store options Forest could adopt:

| Option | Pros | Cons |
|---|---|---|
| ChromaDB | Popular, well-documented | New process, new dep, large install |
| Qdrant | Production-grade, sharded | Separate server process |
| pgvector | SQL-native | Postgres dep (Forest is SQLite) |
| **SQLite-VEC** | In-process, in-SQLCipher, file-portable | Newer (~2024 stable), smaller ecosystem |

SQLite-VEC wins. Forest already runs SQLite (via SQLCipher for
ADR-0050 T2). The extension is a single shared library load on
the existing connection — no new process, no new daemon, no
new wire protocol. The vector data lives **inside the same
encrypted registry.sqlite** as everything else, so encryption-
at-rest covers it automatically.

### Decision 2 — `all-MiniLM-L6-v2` as the canonical embedder

Embedding model options:

| Model | Dimensions | Size | Quality | Notes |
|---|---|---|---|---|
| **all-MiniLM-L6-v2** | 384 | ~80MB | Good | Standard local default |
| all-mpnet-base-v2 | 768 | ~420MB | Better | 2x the storage cost |
| OpenAI text-embedding-3-small | 1536 | (hosted) | Best | Hosted; against ethos |
| BGE-large-en | 1024 | ~1.3GB | Best local | Heavy for M4 mini |

`all-MiniLM-L6-v2` wins on local-first principle + the right
quality/size tradeoff for a 16GB M4 mini. Operators who want
better quality can swap by editing
`core/personal_index.py:DEFAULT_MODEL` — the index code is
embedder-agnostic.

Lazy-import `sentence-transformers` so the daemon boots without
the model loaded. First embed call pays the ~3-5s model-load
cost; subsequent calls are <50ms per document on M-series.

### Decision 3 — Hybrid BM25 + cosine retrieval

Pure cosine search has known failure modes — it ignores rare
terms, mishandles operator-named entities, doesn't surface
exact-match-relevant results. Pure BM25 misses semantic
near-matches.

Hybrid: every query runs BOTH retrievers, the top-K candidates
from each get reciprocal-rank-fusion (RRF) scored, and the
combined ranked list returns.

SQLite has built-in FTS5 for BM25; SQLite-VEC handles the
cosine half. Both run in the same connection on the same data.
Operators get good-quality retrieval without two-store
complexity.

### Decision 4 — Index updates async on memory writes

Memory writes (memory_write.v1, content drafts in D7, knowledge
artifacts in D1) trigger async embedding + indexing. The write
path returns immediately; embedding happens in a background
task. Eventually-consistent retrieval is fine — the operator
notices semantic search after a memory write lags by <1s, not
realtime-strict.

Re-embedding triggered by:
- T2 — first index of a new document
- Operator-initiated reindex (CLI: `fsf index rebuild`)
- Memory consolidation cycles (ADR-0074 queued) when source
  documents change

## Implementation Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | PersonalIndex class + SQLite-VEC schema + add/search/delete + lazy embedder | This burst (B292) | 1 burst |
| T2 | Background indexer worker — memory_write → enqueue embed task | 1 burst |
| T3 | Hybrid BM25 + cosine search with RRF fusion | 1 burst |
| T4 | `personal_recall.v1` builtin tool (cross-domain consumer surface) | 1 burst |
| T5 | `fsf index rebuild` CLI + operator-facing /index/status endpoint | 1 burst |
| T6 | Operator runbook + scaling characterization | 0.5 burst |

Total: 5-6 bursts.

## Consequences

**Positive:**

- Semantic-search across all domains without a separate vector DB.
- Encryption-at-rest covers the index automatically.
- Embedder swap is a one-line change for operators who want
  better quality.
- Hybrid retrieval avoids the well-known failure modes of either
  pure-BM25 or pure-cosine alone.

**Negative:**

- New optional dep on `sentence-transformers` (~500MB on disk
  for the package + model). Operators not enabling semantic
  search don't pay this cost — lazy-imported.
- SQLite-VEC is a relatively new extension. Stable but smaller
  ecosystem than ChromaDB/Qdrant. Mitigated by keeping the
  index code embedder + vector-store-agnostic via thin adapters.
- First-call latency: ~3-5s model load. Mitigated by daemon
  warming the model lazily after first install.

**Neutral:**

- Memory schema gets one new index_status column (none/pending/
  indexed) tracking which entries have been embedded. Pure
  addition; backward-compatible.

## What this ADR does NOT do

- **Does not auto-embed every memory entry.** Operator opts in
  via memory entry tags or domain-level configuration. Default
  off until operator enables — protects against accidental
  background load on a low-memory deployment.
- **Does not handle multi-modal embeddings.** Text only in T1.
  Image/audio embeddings queued for v2 (would need separate
  models).
- **Does not ship a vector-search query language.** The
  `personal_recall.v1` tool takes a plain string query +
  optional filters. Power users who want SQL-level access edit
  via the registry directly.

## See Also

- ADR-0022 Memory subsystem — the entries that get indexed
- ADR-0050 encryption-at-rest — covers the vector table
- ADR-0067 cross-domain orchestrator — primary consumer at T2
- ADR-0073 audit chain segmentation — sister scale ADR
- ADR-0074 memory consolidation — uses the vector index for
  similarity clustering
