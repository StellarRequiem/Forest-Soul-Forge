# Audit chain seqs 7695-7703 fork — May 11 race extension

**Date:** 2026-05-19
**Driver:** B417 (extension of B364)
**HEAD at investigation:** 5357061 (post-B415)

## TL;DR

A second pre-B199 writer-race episode on **2026-05-11** produced
9 duplicate-seq entries: seqs **7695-7703**. Same root cause and
disposition as the May 8 set documented in
`docs/audits/2026-05-17-audit-chain-seq-3728-fork.md`. No
substrate change; `KNOWN_HISTORICAL_FORKS` in section-08 extended
to cover the new range.

## How this surfaced

The Triune-Main wiring_audit_triage run on 2026-05-19 included
`chain_ok=False` in every outcome because `audit_chain_verify.v1`
hits the first fork at seq=3728, returns `broken_at_seq=3728`, and
the skill record-step captures that as the chain verdict.

When investigating to see if a quarantine was needed, a full scan
revealed the May 11 episode that wasn't in `KNOWN_HISTORICAL_FORKS`:

```
DUP line7710 seq=7695 NOSIG scheduled_task_dispatched 2026-05-11T18:22:56Z
DUP line7711 seq=7696 NOSIG tool_call_dispatched      2026-05-11T18:22:56Z
DUP line7712 seq=7697 NOSIG tool_call_succeeded       2026-05-11T18:22:57Z
DUP line7713 seq=7698 NOSIG scheduled_task_completed  2026-05-11T18:22:57Z
DUP line7714 seq=7699 NOSIG scheduled_task_dispatched 2026-05-11T18:27:58Z
DUP line7715 seq=7700 NOSIG tool_call_dispatched      2026-05-11T18:27:58Z
DUP line7716 seq=7701 NOSIG tool_call_succeeded       2026-05-11T18:28:01Z
DUP line7717 seq=7702 NOSIG scheduled_task_completed  2026-05-11T18:28:01Z
DUP line7718 seq=7703 NOSIG scheduled_task_dispatched 2026-05-11T18:33:01Z
```

All unsigned (predate B370). Pattern matches scheduler retries
under contention — same root as May 8.

## Why it's still pre-B199 / not a regression

The 2026-05-11 timestamps are BEFORE B199's per-chain mutex
landed (per ADR-0050 + audit_chain.py ForkScanResult docstring).
Both this episode and the May 8 set fall in the pre-mutex
window where two threads could read `self._head` simultaneously,
both write `seq = head.seq + 1`, and produce orphaned forks.

## Disposition

**Identical to the May 8 disposition.** No substrate change:

1. Do NOT truncate or rewrite the audit chain — the chain is
   append-only and the file is itself an audit-evidence artifact.
2. Do NOT emit a `chain_repair_event` — writer race is fixed
   in B199; nothing to repair forward.
3. **Probe-side fix only:** extend
   `KNOWN_HISTORICAL_FORKS = {3728, 3735, 3736, 3737, 3738, 3740,
   7695, 7696, 7697, 7698, 7699, 7700, 7701, 7702, 7703}` in
   `dev-tools/diagnostic/section-08-audit-chain-forensics.command`.
   Section-08 emits INFO with this audit-doc pointer when verify
   stops at any of those seqs.

## Consequence for wiring_audit_triage

The `chain_ok=False` finding the triune was reasoning about is
NOT operator-actionable; it's a documented pre-B199 artifact.
Future enhancement queued: `wiring_audit.v1` should consult the
same `KNOWN_HISTORICAL_FORKS` (via a shared module or audit
chain config) so its `chain_ok` field reflects "ok modulo known
historical forks" rather than raw verify output. That removes the
recurring high-severity finding from triune triage outputs.

## What would change this disposition

If a NEW duplicate_seq surfaces post-B199 (any seq above ~7710
that wasn't in either May 8 or May 11 sets, dated after B199's
deploy date), THAT is a regression on the mutex fix. Same
investigation playbook as the May 8 doc: verify
`AuditChain._mutex` is held at every write site; rule out
two-instance race; trace the offending write to its caller.

## Cross-references

- `docs/audits/2026-05-17-audit-chain-seq-3728-fork.md` — May 8 episode (B364)
- ADR-0050 — encryption-at-rest + B199 writer mutex fix
- `core/audit_chain.py` ForkScanResult docstring lines 490-518
- `dev-tools/diagnostic/section-08-audit-chain-forensics.command` lines 76+
- CLAUDE.md §0 Hippocratic gate (same reasoning as May 8 doc)
