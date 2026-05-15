# Runbook — PersonalIndex / Semantic Recall (ADR-0076)

**Scope.** Operating the vector index that powers semantic recall
over the operator's `scope='personal'` memory: enabling it,
observing index health, rebuilding from SQL truth, swapping
embedders, and recovering from common failure modes.

**Audience.** Operator on a running daemon.

---

## At a glance

The PersonalIndex is an in-memory store that holds an embedding +
BM25 inverted-index entry for every `scope='personal'` memory
row. The `personal_recall.v1` tool (ADR-0076 T4) reaches for it
via hybrid retrieval — BM25 lexical + cosine semantic, fused via
Reciprocal Rank Fusion. Four ten-domain consumers (Knowledge
Forge / Content Studio / Learning Coach / Research Lab) all use
this surface.

Two pieces of substrate flow data into the index:

1. **At-write background indexer (T2 / B320).** Memory.append
   sees a `scope='personal'` write, enqueues an `IndexerTask` on
   the daemon's async queue, and the worker coroutine drains the
   queue into `PersonalIndex.add()`. Non-blocking; the SQL+chain
   write commits regardless of indexer health.
2. **At-boot rebuild (T5 / B323).** The daemon does NOT
   automatically rehydrate the index from disk on boot — the
   index is in-memory and rebuilds either lazily (the indexer
   walks new writes) or explicitly via `fsf index rebuild`.

The corpus is RESTRICTED to `scope='personal'`. Agent-private
journal entries stay out by design (privacy isolation, B313).
Read access is genre-gated to companion / assistant /
operator_steward / domain_orchestrator.

---

## Enabling the substrate

Off by default. Operator opt-in via env var:

```bash
export FSF_PERSONAL_INDEX_ENABLED=true
```

Then restart the daemon. Lifespan constructs PersonalIndex +
MemoryIndexer, stashes both on `app.state`, awaits
`MemoryIndexer.start()` (spawns the worker coroutine), and feeds
the indexer reference into every Memory instance built by
deps.py. Subsequent personal-scope writes enqueue automatically.

Confirm the substrate is live via `/healthz`'s
`startup_diagnostics` block:

```json
{"component": "personal_index", "status": "ok",
 "message": "PersonalIndex + MemoryIndexer started; ..."}
```

When the env var is unset, the diagnostic reports `status:
"disabled"` and personal_recall.v1 refuses with `substrate
unwired`.

---

## Reading via personal_recall.v1

The tool surface:

```yaml
name: personal_recall
version: "1"
side_effects: read_only
input_schema:
  query: string   # required, non-empty
  limit: integer  # optional, 1..50, default 10
  mode:  enum     # optional, hybrid|cosine|bm25, default hybrid
```

Mode choice:

- **hybrid** (default) — RRF over BM25 + cosine. Most balanced.
  Use this unless you have a specific reason not to.
- **cosine** — embedding-only. Best for paraphrase / synonym
  matching when the operator's wording differs from the stored
  entry's wording.
- **bm25** — lexical-only. Best for exact phrases, proper nouns,
  numbers (account numbers, dates, names). The embedder blurs
  these; BM25 doesn't.

Output:

```json
{
  "count": 3,
  "mode":  "hybrid",
  "hits":  [
    {"doc_id": "...", "text": "...", "source": "memory:episodic:personal",
     "tags": ["habit"], "similarity": 0.0312}
  ]
}
```

`similarity` is a within-retrieval relative-order signal. Across
modes the numbers aren't comparable (RRF score, cosine, BM25 raw
score all have different ranges). Rank order within ONE retrieval
is what matters.

**Privacy invariant.** The raw query NEVER lands on the audit
chain. Only `query_hash` (SHA-256 truncated 16 chars) +
`mode/limit/hit_count` are recorded.

---

## Observing index health

Today the substrate exposes operator counters in code only:

```python
indexer.status()  # via app.state.memory_indexer
# -> {"enqueued": N, "indexed": N, "failed": K,
#     "queue_depth": Q, "running": bool}
```

A `/memory/indexer/status` HTTP endpoint is queued for a future
tranche; until it lands, attach a debugger or print the snapshot
from a control script.

Health rule-of-thumb:

| Symptom | What to do |
|---|---|
| `failed > 0` | Embedder is throwing — usually missing
  sentence-transformers install or model file. Check daemon logs. |
| `queue_depth` growing | Worker can't keep up with writes.
  Embedder slow or stuck. Restart daemon. |
| `enqueued > 0 && indexed == 0 && running == false` | Worker died.
  Investigate; restart daemon. |
| `enqueued == 0` after writes | Memory.append doesn't see the
  indexer. Check `deps.py` is passing `app.state.memory_indexer`
  through to Memory(...). |

For correctness checks, compare SQL truth to index count:

```bash
fsf index status
# scope='personal' entries eligible for indexing: 247
```

If the index `count()` is materially below the eligible count,
**rebuild**.

---

## Rebuilding the index

Reasons to rebuild:

1. **Embedder swap.** Changed `FSF_PERSONAL_INDEX_MODEL` →
   dimensions probably changed → existing vectors are now
   nonsense. Always rebuild after model swap.
2. **Consolidation merge.** ADR-0074 ran and folded old episodic
   rows into a summary; the source rows are now
   `state='consolidated'` but the index still has their vectors.
   The summary entry has its own indexer-fired vector but the
   stale source vectors will surface in recall. Rebuild to drop
   them.
3. **Backup restore.** Restored a chain that pre-dates ADR-0076
   T2 — no indexer events ever fired for those entries. Rebuild
   to populate.
4. **Daemon downtime.** Personal-scope writes that landed via
   direct SQL (CLI patches, restore scripts) never went through
   Memory.append's hook. Rebuild after any out-of-band write.

### Procedure

```bash
# 1. Stop the daemon. Concurrent writes would race the rebuild.
launchctl stop com.forest.soul-forge
# or: pkill -f forest_soul_forge.daemon

# 2. Inspect first.
fsf index status

# 3. Dry-run to size the rebuild.
fsf index rebuild --dry-run

# 4. Real rebuild.
fsf index rebuild
# loading embedder + initializing index (batch_size=32)...
# rebuild complete in 4.2s: indexed=247 failed=0 count=247

# 5. Restart the daemon.
launchctl start com.forest.soul-forge
```

The first `add()` after model construction pays the embedder
cold-load cost (~3-5s for sentence-transformers MiniLM). Once
loaded the model stays warm for the rest of the rebuild + the
daemon process lifetime.

### Encrypted rows

The CLI is the **offline plaintext path**. When at-rest
encryption is on (B269), encrypted rows in `memory_entries` are
skipped — the master key is daemon-resident. To rebuild an
encrypted corpus you'd need to either:

- Decrypt-on-disk via `fsf encrypt rotate-master-key --to=plaintext`
  (destructive — only for tests / disaster recovery).
- Build a daemon-side rebuild endpoint that consumes
  `app.state.master_key` for decryption (deferred to a future
  tranche when an operator actually hits this case in production).

---

## Swapping embedders

The default is `all-MiniLM-L6-v2` (384 dimensions, ~80MB on
disk). Operators can swap to a larger or smaller model via env
var:

```bash
export FSF_PERSONAL_INDEX_MODEL=all-mpnet-base-v2
```

Dimensions change. Always rebuild after swapping. PersonalIndex's
constructor sets `self.embedder.dimensions` from the loaded
model; once an index has vectors at dim=384, you cannot add new
ones at dim=768 — `_cosine` raises on dimension mismatch.

The model itself is hot-loaded from HuggingFace by
`sentence_transformers` on first add()/search(). The download
goes to the operator's `~/.cache/huggingface/`. Pre-pull the
model on a network-attached host before deploying offline:

```bash
python3 -c "from sentence_transformers import SentenceTransformer; \
            SentenceTransformer('all-mpnet-base-v2')"
```

---

## Recovery — common failure modes

### "personal index not wired"

The personal_recall.v1 tool refuses with this when
`ctx.personal_index` is None. Causes:

- `FSF_PERSONAL_INDEX_ENABLED` not set → enable it + restart.
- Substrate failed to construct → check `/healthz` for the
  `personal_index` diagnostic with `status: "failed"` and an
  error string. Common causes: sentence-transformers not
  installed, model file unreadable, dimension mismatch from
  a partial migration.

### Indexer worker died

`status()` shows `running: false` but `enqueued > indexed`.
The dispatcher's run_in_executor caught a fatal exception and
killed the worker. Restart the daemon — the lifespan rebuilds
the worker. Queued tasks at death are abandoned; rebuild
afterward to fill them in.

### Recall returns wrong / stale rows

Most likely cause: index drift from SQL truth. Compare via
`fsf index status` against the in-memory `count()`. If they
differ, rebuild.

Second cause: the operator's wording is genuinely different from
the stored entry's wording. Try `mode='cosine'` to lean on
semantic similarity, OR `mode='bm25'` to demand the exact term.
Hybrid (default) tries both but the RRF math doesn't always
elevate the right hit on tiny corpora.

### Genre rejection

Tool refuses with "genre … not authorized" — the calling agent's
genre isn't in `PERSONAL_SCOPE_ALLOWED_GENRES`. By design. Only
companion / assistant / operator_steward / domain_orchestrator
can read the operator's personal context. If you genuinely need
another genre to read, the right move is to expose the relevant
fact via a different surface (operator_profile_read.v1, or a
dedicated tool) — NOT to widen this gate.

---

## Reference

- ADR-0076 — Vector Index for Personal Context (canonical spec)
- ADR-0068 T3 (B313) — personal scope + genre allow-list
- ADR-0076 T1 (B292) — PersonalIndex substrate
- ADR-0076 T2 (B320) — MemoryIndexer hook
- ADR-0076 T3 (B321) — hybrid BM25+cosine RRF
- ADR-0076 T4 (B322) — personal_recall.v1 tool
- ADR-0076 T5 (B323) — `fsf index` CLI
