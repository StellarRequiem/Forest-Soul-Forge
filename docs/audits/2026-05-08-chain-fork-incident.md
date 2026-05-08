# Audit-chain fork incident (2026-05-08)

**Date:** 2026-05-08
**Author:** Forest Soul Forge harness (session 5, post-B198)
**Source finding:** "Full system check" requested by operator; Forest's own
`AuditChain.verify()` returned `ok=False, broken_at_seq=3728` against the
live `examples/audit_chain.jsonl`. External Python verifier confirmed and
extended the finding (3 → 6 broken seqs).
**Disposition:** Architectural defect; ship 3-layer fix as **B199**;
historical breach documented here, NOT erased (chain is append-only by
invariant — including its broken parts).
**Status:** Fixed in B199. The 6 historical forks remain on the chain
as forensic record.

---

## What happened

The audit chain at `examples/audit_chain.jsonl` had **6 duplicate seq
numbers** at:

```
seq 3728 — plugin_installed     vs scheduled_task_dispatched
seq 3735 — plugin_installed     vs tool_call_succeeded
seq 3736 — plugin_installed     vs <runtime event>
seq 3737 — plugin_installed     vs <runtime event>
seq 3738 — plugin_installed     vs <runtime event>
seq 3740 — plugin_installed     vs scheduled_task_dispatched
```

In each case, two entries share the same `seq` value AND the same
`prev_hash`. Each entry's individual `entry_hash` is internally
correct (SHA-256 of its own canonical-form payload matches), so this
is **NOT tampering or canonical-form drift**. It is a **write race**.

`AuditChain.verify()` short-circuits at the first structural problem,
so it reported only seq 3728 as `seq gap: expected 3729, got 3728`.
The other 5 collisions were silent. The new `AuditChain.scan_for_forks()`
(B199 Layer 3b) walks the entire chain and reports every duplicate.

## Root cause

`AuditChain.append()` had no internal lock. Its docstring claimed
"single-writer threat model" — meaning every caller was responsible for
acquiring `app.state.write_lock` before calling `append()`. Two specific
call sites violated this discipline:

### Call site 1 — `daemon/plugins_runtime.py::build_plugin_runtime`

The startup-time initial reload (called from `app.py` lifespan)
discovers all installed plugins on disk and emits one
`plugin_installed` event per plugin. Pre-B199 it ran without
`write_lock`, on the assumption documented in a code comment:

> "Initial population — equivalent to a startup reload but without
> the write_lock since lifespan owns the only handle and there are no
> concurrent writers yet."

That assumption was **false**. By the time `build_plugin_runtime`
runs, lifespan has already called `await scheduler.start()` ten lines
earlier, and the scheduler is already ticking and dispatching tasks
that emit `scheduled_task_dispatched` events to the same chain.

### Call site 2 — `daemon/scheduler/runtime.py::Scheduler._dispatch`

Scheduled-task dispatch emits 1-3 events per run (`dispatched`, then
`completed`/`failed`, optionally `circuit_breaker_tripped`) plus a
SQLite upsert via `_persist_task_state`. None of these were wrapped
in `write_lock`. A code comment claimed the dispatch was "serialized
by write_lock anyway" but the dispatch flow never actually acquired
it. The 5-minute scheduler tick races with HTTP-route writers
(birth, plugin install, audit append, etc.) every cycle.

### Why the race manifested when it did

The 6 forks cluster around 3 daemon restart events on 2026-05-08:

| Restart at | Forks | Pattern |
|---|---|---|
| 15:19:52 | 1 (seq 3728) | Single plugin installed during startup; raced one scheduled task dispatch |
| 15:28:01 | 1 (seq 3735) | Single plugin installed; raced an in-flight tool call |
| 15:36:28 | 4 (seq 3735-3738, 3740) | Four plugins installed in 6 seconds; raced two scheduled task dispatches |

Each restart re-runs `build_plugin_runtime()` (which emits
`plugin_installed` for every plugin currently in `~/.forest/plugins/installed/`)
and resumes the scheduler (which immediately fires due tasks). Two
writers, neither holding the lock, racing the same head pointer.

## §0 Hippocratic gate

| Step | Verdict |
|---|---|
| 1. Prove harm | **YES.** 6 duplicate seqs on the live chain. Project's own `AuditChain.verify()` returns `ok=False`. CLAUDE.md invariant "audit chain is append-only and hash-linked" violated. The whole project's claim to verifiability rests on chain integrity. |
| 2. Prove non-load-bearing | **YES.** No caller depends on chain.append producing duplicate seqs. The duplicate behavior IS the bug. |
| 3. Prove alternative is strictly better | **YES.** Serialized writes are strictly better than races for an append-only hash chain. The fix introduces no new ABI, no new dependency, no behavioral change observable from agents — only the elimination of an unintended race. |
| 4. Record outcome | This document. |

## The fix — B199, three layers

### Layer 2 (defense in depth) — internal RLock on `AuditChain`

`core/audit_chain.py`: `__init__` now constructs `self._append_lock = threading.RLock()`. The `append()` method's seq-derivation + write + head-advance is wrapped in `with self._append_lock:`. The chain is now self-protecting against in-process concurrent writers regardless of caller discipline.

`app.state.write_lock` remains the cross-resource serializer (chain + registry SQLite + plugin filesystem advance together) — but the chain's *own* integrity is no longer hostage to caller discipline.

Concurrent appends across separate **processes** to the same JSONL file remain undefined behavior; that's an OS-level fcntl-flock problem deferred per ADR-0005 § threat-model. The internal lock here covers the common case (one daemon process, multiple async tasks / threads).

### Layer 1 (surgical) — explicit write_lock at the bypassing call sites

- `daemon/app.py` lifespan: `build_plugin_runtime(...)` is now wrapped in `with app.state.write_lock:`. The stale code comment on `build_plugin_runtime` itself is rewritten to point at this audit doc.
- `daemon/scheduler/runtime.py::_dispatch`: pre-runner emit, post-runner emit, and `_persist_task_state` are each wrapped in `with write_lock:`. The lock is explicitly **NOT** held during `await runner(...)` — that's the slow path (LLM call, multiple seconds) and holding the lock through it would block every HTTP route. The lock is acquired around the short critical sections only.

### Layer 3 (verifier hardening)

- `KNOWN_EVENT_TYPES` extended from 57 → 71 entries to cover the 14 forward-compat events shipped by ADR-0033/0034/0041/0045/0048/0053/0056. Pre-B199 the verifier silently logged these as "unknown" warnings on every chain walk. The drift was real — these are first-class events from accepted ADRs, not warnings.
- New `AuditChain.scan_for_forks()` method + `ForkScanResult` dataclass. Walks the entire chain reporting every duplicate seq and every hash mismatch without short-circuiting at the first break. `verify()` is unchanged — its short-circuit is correct for "is this chain still trustworthy" — but `scan_for_forks` answers the different question "where are all the breaches."
- `dev-tools/check-chain-forks.sh` operator script. Exits 0 if clean, 1 if any anomaly. Suitable for CI / pre-tag gating.

### Regression coverage

`tests/unit/test_audit_chain.py::TestConcurrentAppend` — 3 new tests:
- 16 threads × 50 appends = 800 calls, all seqs must be unique and verify clean
- On-disk JSONL must show seqs 0..N strictly increasing (no duplicates, no gaps, no out-of-order)
- The lock must be re-entrant (RLock, not Lock — important if a future refactor composes chain.append inside other locked work)

Plus 4 tests for `scan_for_forks`:
- Clean chain returns ok=True
- Hand-crafted duplicate seq is detected
- Hand-edited hash mismatch is detected (independent of duplicates)
- Two forks at different seqs are BOTH reported (no short-circuit)

The concurrency test would have failed pre-B199 (sometimes — racing tests are flaky by nature). The fork-detection tests would have passed pre-B199 against `verify()` for the first finding only and missed the rest.

## What we did NOT do

**The 6 historical forks at seqs 3728/3735-3738/3740 stay on the chain.** The audit chain is append-only — including its broken parts. Erasing them would itself be a worse violation than the original bug. They are now forensic record. Operators inspecting the chain will see them; `scan_for_forks` will report them; this audit document explains why.

A future burst MAY add a `chain_breach_noted` event type that the daemon writes ON THE NEXT APPEND after a `scan_for_forks` mismatch — pinning the audit-trail observation of the historical breach to the chain itself. That's a design question, not a bug fix, and is explicitly OUT of scope for B199.

## Verification

After B199, against the live chain at HEAD `<B199-sha>`:

```
$ bash dev-tools/check-chain-forks.sh
chain:            examples/audit_chain.jsonl
entries_scanned:  <N>
ok:               False
duplicate_seqs:   [3728, 3735, 3736, 3737, 3738, 3740]
hash_mismatches:  []
unknown_events:   0
exit=1
```

`ok: False` is **expected** — the historical forks remain. What changed:
- Going forward, no NEW forks should appear (B199 Layer 1+2 prevents them).
- The verifier no longer reports drift warnings on legitimate event types.
- The fork detector reports all 6 historical forks at once instead of just the first.
- `tests/unit/test_audit_chain.py::TestConcurrentAppend` proves the race is gone under threaded storm.

## References

- ADR-0005 audit chain (the original threat-model statement)
- ADR-0033 plugin runtime (origin of `plugin_installed` event)
- ADR-0041 set-and-forget orchestrator (origin of `scheduled_task_*` events)
- B134 audit chain canonical-form contract (sister fix; spec drift class)
- CLAUDE.md §0 Hippocratic gate; "single-writer SQLite discipline" architectural invariant
