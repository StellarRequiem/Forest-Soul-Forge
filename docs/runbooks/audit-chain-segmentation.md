# Runbook — Audit Chain Segmentation (ADR-0073)

**Scope.** Operating the audit-chain segmentation substrate at
the ten-domain platform's projected millions-of-events scale:
running the one-shot migration, scheduling the monthly sealing
runner, verifying segment integrity, and responding to tamper
indicators.

**Audience.** Operator on a running daemon (or about to start one).

---

## At a glance

ADR-0073 splits the previously-monolithic `audit_chain.jsonl`
into monthly segment files:

```
examples/
├── audit_chain.jsonl              ← legacy monolithic chain (pre-migration)
├── audit_chain_index.json         ← segment index (post-migration)
├── audit_chain_2026-03.jsonl      ← sealed segment
├── audit_chain_2026-04.jsonl      ← sealed segment
└── audit_chain_2026-05.jsonl      ← current tail (writes go here)
```

The **index** carries one `SegmentMeta` per file: `seq_start`,
`seq_end`, `sealed`, `merkle_root`. Sealed segments are frozen —
their `merkle_root` lets a verifier confirm tamper-freedom in
one hash check instead of walking every line. The tail segment
is mutable: it grows as new events append, and gets sealed on
the next monthly pass.

Three operator-visible audit events bracket the lifecycle:

- `audit_chain_anchor` — emitted at seal time; carries the
  sealed segment's filename, last seq, Merkle root, and entry
  count.

---

## First-time migration (one-shot)

Existing operators with a monolithic `audit_chain.jsonl` migrate
once via `migrate_monolithic_chain()`. Until a `fsf audit
segment-migrate` CLI lands, the canonical path is a short script:

```python
from pathlib import Path
from forest_soul_forge.core.audit_chain_segments import (
    migrate_monolithic_chain, save_segment_index,
)

outcome = migrate_monolithic_chain(
    source_path=Path("examples/audit_chain.jsonl"),
    segment_dir=Path("examples/"),
)
save_segment_index(outcome.new_index, Path("examples/audit_chain_index.json"))
print(f"Migration: {outcome.segments_created} segments created")
print(f"Per-month entries: {outcome.entries_written}")
```

What the migration does:

1. Reads `audit_chain.jsonl` line by line.
2. Groups entries by `YYYY-MM` from each entry's `timestamp`.
3. Writes one `audit_chain_YYYY-MM.jsonl` per month.
4. Builds the index with every month-segment sealed=True
   (Merkle root computed at migration time) EXCEPT the most
   recent month, which becomes the new tail (sealed=False).
5. Leaves the original `audit_chain.jsonl` **byte-identical** so
   you can roll back by deleting the new files and the index.

After migration, point `FSF_AUDIT_CHAIN_PATH` at the tail
segment file (e.g. `examples/audit_chain_2026-05.jsonl`) so new
appends land in the right place. **Restart the daemon** for the
env-var change.

---

## Ongoing monthly sealing

Once migrated, the `seal_audit_segment_runner` seals the current
tail when its month is past. The intended cadence is once per
month, soon after midnight UTC on the 1st.

### Manual operator invocation

```python
import asyncio
from pathlib import Path
from forest_soul_forge.core.audit_chain_segments import (
    seal_audit_segment_runner,
)
from forest_soul_forge.core.audit_chain import AuditChain

chain = AuditChain(Path("examples/audit_chain_2026-05.jsonl"))
result = asyncio.run(seal_audit_segment_runner(
    index_path=Path("examples/audit_chain_index.json"),
    segment_dir=Path("examples/"),
    audit_chain=chain,
))
print(result)
```

Returns a `SealRunResult` with `ok` + `sealed_segment_file` +
`next_segment_file` + the anchor payload that landed on chain.

Common `no_op_reason` values:

- `tail_is_current_month` — tail is the current UTC month;
  sealing now would split events that belong together. Pass
  `force=True` to override (rare, only when you're catching up
  on a long-paused chain).
- `no_tail_segment` — the index is missing or has no unsealed
  segment. Either you haven't migrated yet, or someone deleted
  the index without doing a fresh migration.
- `seal_failed: <reason>` — `seal_segment` refused (malformed
  segment file, missing required field). Surface the message;
  the underlying segment file needs inspection.
- `anchor_emit_failed: <reason>` — the seal landed on disk but
  the `audit_chain_anchor` emit failed. The new tail is in
  place, the index is current; you need to manually re-emit
  the anchor (or accept the gap, which a future verify will
  surface).

### Scheduled wiring

Add to `config/scheduled_tasks.yaml`:

```yaml
- id: audit_chain_seal_monthly
  description: "Seal the audit chain's prior-month segment on the 1st"
  schedule: "every 168h"   # weekly check is sufficient; the runner's
                           # tail_is_current_month guard skips when
                           # nothing to do
  enabled: true
  type: tool_call           # uses the existing tool_call runner
  config:
    agent_id: maintenance_001
    tool_name: audit_chain_seal
    tool_version: "1"
    args: {}
```

(The `audit_chain_seal.v1` tool wrapper around the runner lands
as a follow-on burst; for now the runner is callable from code
+ a manual script.)

Budget cap from ADR-0075 applies: by default `budget_per_minute=6`
which is comfortable for a once-a-week invocation. No tuning
needed unless you're running multiple Forest instances against
one shared chain (don't — single-writer discipline).

---

## Verifying segment integrity

Run `verify_sealed_segments` (B301) to spot-check that no
sealed segment's file has been tampered with:

```python
from pathlib import Path
from forest_soul_forge.core.audit_chain_segments import (
    load_segment_index, verify_sealed_segments,
)

idx = load_segment_index(Path("examples/audit_chain_index.json"))
result = verify_sealed_segments(
    index=idx, segment_dir=Path("examples/"),
)
print(f"ok={result.ok}, verified={result.segments_verified}")
for issue in result.issues:
    print(f"  {issue.kind} on {issue.segment_file}: {issue.details}")
```

Run on every backup restore, before a release tag, and as a CI
smoke step. The issue kinds (B301):

- `merkle_mismatch` — file content's Merkle root no longer
  matches the stored root. **Tamper signal.** Cross-reference
  against the on-chain `audit_chain_anchor` event for that
  segment to confirm the stored root + investigate the file.
- `file_missing` — sealed segment file gone from disk. Restore
  from backup.
- `no_root` — segment marked sealed but `merkle_root` is null
  in the index. Schema violation, not a tamper signal — chase
  the index file for corruption.
- `scan_error` — segment file is malformed (bad JSON, missing
  entry_hash). File is broken in a different way than missing.

---

## Tamper response

If `verify_sealed_segments` returns `merkle_mismatch`:

1. **Don't write to the chain yet.** New writes would compound
   the divergence.
2. Pull the on-chain `audit_chain_anchor` for the affected
   segment (search the chain for
   `event_data.prior_segment_file == <bad_file>`). The anchor's
   `prior_merkle_root` is the operator's source of truth — it
   was signed at seal time (if ADR-0049 is active) so even an
   attacker who edited the segment file couldn't forge a matching
   anchor.
3. Compare the anchor's Merkle root against the
   recomputed-from-file root. If they DIFFER → the file was
   tampered with after sealing. Restore from backup, the
   on-chain anchor confirms which version was canonical.
4. If they MATCH → the index file's stored root was corrupted
   (less common); rewrite the index with the value from the
   anchor.
5. After resolution, append a `chain_repair` event to the chain
   documenting the operator action. Future audit reads will
   show the repair in context.

---

## Performance posture

Migration is O(N) over the existing chain — at 1M entries
expect ~3-5 seconds. Sealing is O(M) over the current tail
segment, where M is one month's growth — typically ~1-10K
events, ms-scale. Verification of sealed segments is O(K) where
K is the segment count — at year 3 (~36 segments) full
verification is <1 second.

The Merkle root computation is the hot loop in both seal +
verify. Standard binary Merkle, sha256 per level, level
halving — measured at ~300K hashes/sec on M-series Mac.

---

## See also

- ADR-0073 — audit chain segmentation (this runbook's home ADR)
- ADR-0049 — per-event signatures (the anchor's signed proof of
  pre-tamper state)
- ADR-0050 — encryption-at-rest (segment files are envelope-
  encrypted if encryption is on)
- ADR-0075 — scheduler scale (the budget cap the scheduled
  runner respects)
- `docs/runbooks/encryption-at-rest.md` — sibling Phase α runbook
- `docs/runbooks/memory-consolidation.md` — sibling Phase α runbook
- `docs/runbooks/scheduler-scale.md` — sibling Phase α runbook
