# Audit chain seq 3728 fork — investigation and disposition

**Date:** 2026-05-17
**Driver:** B364
**HEAD at investigation:** 7afcdcb (post-B370)

## What the harness reported

`diagnostic-all` section 08 reports:

```
[FAIL] audit_chain_verify end-to-end — broken_at_seq=3728,
       reason=seq gap: expected 3729, got 3728
```

The harness was flagging this as the canonical "audit chain integrity
break" pattern — every entry past 3728 looked suspect.

## What's actually there

Direct read of `examples/audit_chain.jsonl` around the break:

| File line | seq | event_type | timestamp | entry_hash | prev_hash |
|---:|---:|---|---|---|---|
| 3726 | 3725 | tool_call_dispatched | 2026-05-08T15:17:22Z | 3f38… | eab8… |
| 3727 | 3726 | tool_call_succeeded | 2026-05-08T15:17:25Z | 9e6a… | 3f38… |
| 3728 | 3727 | plugin_installed | 2026-05-08T15:19:52Z | 572c… | 9e6a… |
| **3729** | **3728** | **plugin_installed** | **2026-05-08T15:19:52Z** | **a466…** | **572c…** |
| **3730** | **3728** | **scheduled_task_dispatched** | **2026-05-08T15:22:52Z** | **dd8e…** | **572c…** |
| 3731 | 3729 | tool_call_dispatched | 2026-05-08T15:22:52Z | f0b4… | dd8e… |
| 3732 | 3730 | tool_call_succeeded | 2026-05-08T15:22:55Z | 4bb4… | f0b4… |

Two entries share `seq=3728` (file lines 3729 and 3730). Both
claim `prev_hash=572c…` (seq 3727's entry_hash). This is a
fork: two threads both read `head=seq 3727` and both wrote an
entry with `seq = head.seq + 1`. Whichever wrote second won the
"next" position — seq 3729 onward references `dd8e…` (the
scheduled_task entry's hash) as its `prev_hash`. The
`plugin_installed` entry at file line 3729 (`a466…`) is
orphaned — nothing in the chain references its `entry_hash`.

## Why it's already known

`src/forest_soul_forge/core/audit_chain.py:497-507` documents
this exact pattern in the `ForkScanResult` dataclass docstring:

> `duplicate_seqs` — sequence numbers that appear in more than
> one entry. Signature of a write race where two threads grabbed
> the same `self._head` and both wrote with `seq = head.seq + 1`.
> **The pre-B199 forks at chain seqs 3728 / 3735-3738 / 3740 are
> the canonical example.**

ADR-0050 B199 introduced the per-chain mutex that prevents this
race in the writer. Forks emitted before that fix are immutable
historical record — the chain is append-only, so they stay.

## Disposition

**No substrate change.** Specifically:

1. Do NOT truncate or rewrite the audit chain — the chain is
   append-only and the file is itself an audit-evidence artifact.
   Mutating it would create exactly the kind of corruption the
   chain is meant to detect.
2. Do NOT emit a `chain_repair_event` — the writer race is fixed
   (B199); there's nothing to repair forward. The forks are
   historical and the verifier already classifies them via
   `ForkScanResult.duplicate_seqs`.
3. **Probe-side fix only:** section-08 now has a
   `KNOWN_HISTORICAL_FORKS = {3728, 3735, 3736, 3737, 3738, 3740}`
   set. When `audit_chain_verify` reports `broken_at_seq` in that
   set, the section emits INFO with a pointer to ADR-0050 instead
   of FAIL. Any NEW broken seq not in the set continues to FAIL
   loudly.

This preserves visibility (operator still sees the historical
fork report) without polluting drift detection.

## What would change this disposition

If a NEW writer race surfaces (a new duplicate_seq outside the
known set, post-B199), that IS a regression on the mutex fix.
Section 08 will catch it as FAIL on first run; investigation
should:

1. Verify B199's per-chain mutex (`AuditChain._mutex`) is held
   at the write site — every caller MUST go through the
   write_lock-acquired path per CLAUDE.md's "single-writer SQLite
   discipline" extended to the chain.
2. Check if a process was spawned that constructed a SECOND
   `AuditChain(path)` instance — the mutex is per-instance, so a
   second instance bypasses it. The dispatcher's single chain
   instance via `ToolContext.audit_chain` (B350-wired) is the
   load-bearing safeguard.

## Cross-references

- ADR-0050 (encryption-at-rest + B199 chain writer fix)
- core/audit_chain.py ForkScanResult docstring lines 490-518
- CLAUDE.md §0 Hippocratic gate (this disposition exercises
  steps 1-3: prove harm = false-positive drift; prove non-load-
  bearing = chain semantic untouched; prove alternative = the
  no-truncation rule is itself the right call).
