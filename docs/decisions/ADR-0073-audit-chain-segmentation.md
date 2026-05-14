# ADR-0073 — Audit Chain Segmentation

**Status:** Accepted (2026-05-14). Phase α scale substrate. Sized
to handle the projected ~10K-100K events/day the ten-domain
platform will produce once telemetry streams come online.

## Context

The live audit chain at `examples/audit_chain.jsonl` is a single
append-only file. Today it sits at ~11,000 entries (~10 MB). With
the ten-domain platform arc + ADR-0064 telemetry pipeline (queued)
projected to produce 10K-100K events/day, the chain hits millions
of entries in weeks. Three problems surface:

1. **Linear walk-from-genesis to verify is unworkable.** Every
   `audit_chain_verify.v1` invocation reads the whole file +
   re-walks every prev_hash. At 10M entries that's minutes per
   verify — too slow for the dispatcher's per-call discipline.

2. **File handle + memory locality.** Tools that scan the chain
   (B256's tail-based pattern; B254's conversation reads) need
   to keep the hot tail in memory. A monolithic file forces them
   to seek-from-end + buffer-walk, which works but limits
   parallelism.

3. **Encryption-at-rest envelope per line.** ADR-0050 T3 envelope-
   encrypts each entry. Re-keying the chain (T8 rotate-key) needs
   to rewrite every line under a new master key. A monolithic
   file means rotation is a single atomic rewrite — risky AND
   expensive. Segments let rotation proceed segment-by-segment.

## Decision

This ADR locks **four** decisions:

### Decision 1 — Monthly segment files

The chain splits into monthly files:
- `examples/audit_chain_2026-05.jsonl` — May entries
- `examples/audit_chain_2026-06.jsonl` — June entries
- ...

Plus an index file:
- `examples/audit_chain_index.json`

That maps `seq_range → segment_file`:
```json
{
  "schema_version": 1,
  "segments": [
    {"seq_start": 1, "seq_end": 8742, "file": "audit_chain_2026-05.jsonl",
     "month": "2026-05", "sealed": true,
     "merkle_root": "abc123..."},
    {"seq_start": 8743, "seq_end": null, "file": "audit_chain_2026-06.jsonl",
     "month": "2026-06", "sealed": false}
  ]
}
```

Monthly is the right granularity: small enough that segments stay
queryable, large enough that index updates are infrequent (one
new entry per month + the seq_end update on the sealing segment).
Operators can override via `FSF_AUDIT_CHAIN_SEGMENT_MONTHS=N` if
they want larger windows.

### Decision 2 — Anchor entries bridge segments

The first entry of each new segment is a special
`audit_chain_anchor` event whose `event_data` carries:
- `prior_segment_file` — name of the segment being sealed
- `prior_seq_end` — last seq in the prior segment
- `prior_merkle_root` — Merkle root over every entry_hash in the
  prior segment
- `prior_segment_entry_count` — number of entries

The anchor's `prev_hash` references the final entry's
`entry_hash` from the prior segment. So the linked-list invariant
holds across segment boundaries: prev_hash of anchor =
entry_hash of prior segment's last entry.

The anchor is also signed (ADR-0049 per-event signatures); its
signing key is the operator's master signing key, not an agent's
DNA. This means **anchors are unforgeable** — even an attacker
with full read access to the audit chain can't fabricate a
matching anchor across segments without the operator's key.

### Decision 3 — Verify-from-head walks only the current segment + checks anchors

A standard `audit_chain_verify.v1` invocation now:

1. Loads the current segment (tail of the chain).
2. Walks every entry's prev_hash chain to genesis OF THE CURRENT
   SEGMENT.
3. Cross-references the first entry's `prev_hash` against the
   anchor entry, which carries `prior_merkle_root`.
4. EITHER trusts the anchor's Merkle root (fast verify; operator
   accepts that prior segments are sealed) OR descends into the
   prior segment(s) for full-walk verify (slow but complete).

Two verify modes:
- `mode=tail` — current segment + anchor signatures only.
  O(segment_size). Default for per-dispatch RealityAnchor
  consultation.
- `mode=full` — every segment from genesis. O(chain_size). Used
  by drift sentinel + the conformance suite's chain-integrity
  test.

### Decision 4 — Sealed segments stay encrypted but lazy-loaded

ADR-0050 T3 envelope-encrypts per entry. Sealed segments
(`sealed: true` in the index) get re-keyed lazily during T8
rotation (ADR-0050) — operators can take the daemon offline,
rotate one sealed segment at a time, bring the daemon back up.
The tail segment stays under the current key for live appends.

Sealed segments are read-only by daemon discipline. Any tool
that needs to read older entries goes through
`audit_chain_segment_reader` (new helper in T1) which lazy-opens
+ caches the segment file. Memory cost: one segment in memory at
a time during a scan; most operations only touch the tail.

## Implementation Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Index format + segment writer + segment reader + anchor entry shape | This burst (B291). Foundation. | 1 burst |
| T2 | Sealing flow — when a new month starts, freeze the prior segment + emit anchor + Merkle root | 1 burst |
| T3 | audit_chain_verify.v1 extended with mode=tail / mode=full | 1 burst |
| T4 | Migration helper — convert the existing monolithic chain to segmented (one-shot) | 1 burst |
| T5 | Operator runbook + scaling characterization (verify times at 1M / 10M / 100M entries) | 0.5 burst |

Total: 4-5 bursts.

## Consequences

**Positive:**

- Per-dispatch verify cost drops from O(chain) to O(segment).
- Encryption rotation becomes incremental (one segment at a time).
- Operator can archive old segments off-disk without losing
  verifiability (the merkle_root in the index stays).
- Index file is small + queryable; tools can answer "give me
  the entries between 2026-05 and 2026-08" without reading any
  segment.

**Negative:**

- Migration from monolithic chain → segmented requires one-time
  daemon stop + tool run. T4 ships the migration helper.
- New invariant to maintain: anchor's prev_hash must match the
  prior segment's last entry_hash. T2 enforces.
- Two more files per month (the segment + the index update).
  Operators with concurrent backups need to update their backup
  scripts.

**Neutral:**

- Doesn't change the per-entry hash chain invariant — entries
  inside a segment chain to each other identically to today.
- Doesn't change ADR-0049 per-event signatures.
- Doesn't change ADR-0050 envelope encryption.

## What this ADR does NOT do

- **Does not migrate automatically.** T4 ships an operator-run
  migration. The daemon doesn't silently rewrite the chain.
- **Does not delete old segments.** Sealed segments stay until
  the operator explicitly archives them. Even archived segments
  can be brought back online by editing the index.
- **Does not change the audit-chain canonical path.** The default
  still resolves to `examples/audit_chain.jsonl`; post-migration
  that file becomes the current month's segment, and the
  index points at it.

## See Also

- ADR-0003 Audit Chain (the canonical invariants this layer
  preserves)
- ADR-0049 per-event signatures (anchors get the same signature
  discipline)
- ADR-0050 encryption-at-rest (segments encrypt independently)
- ADR-0074 memory consolidation (sister scale ADR for
  memory_entries)
