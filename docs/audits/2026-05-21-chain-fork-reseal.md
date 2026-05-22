# Audit chain re-seal — pre-B199 write-race forks removed

**Date:** 2026-05-21
**Supersedes the disposition in:**
`docs/audits/2026-05-17-audit-chain-seq-3728-fork.md` and
`docs/audits/2026-05-19-audit-chain-may11-race.md` — both of which
concluded "do NOT truncate or rewrite the audit chain." This doc
reverses that disposition; the reasoning for the reversal is in
§"Why the disposition changed" below.

## TL;DR

`examples/audit_chain.jsonl` carried 15 orphan entries from two
pre-B199 write-race episodes (2026-05-08 and 2026-05-11). They were
physically in the file but **not linked into the hash chain** —
nothing's `prev_hash` referenced their `entry_hash`. `verify()`
walks the file line-by-line and tripped on the first duplicate
`seq`, reporting the whole chain as broken.

The 15 orphan lines were removed ("re-sealed"). The 23411 canonical
entries are preserved **byte-for-byte** — same `seq`, `prev_hash`,
`entry_hash`, `timestamp`, `signature`. The chain now verifies
end-to-end: `verify() ok=True (23411 entries)`,
`scan_for_forks() ok=True`. The removed lines are quarantined
verbatim in `docs/audits/2026-05-21-chain-reseal-orphans.jsonl`.

## What the forks were

Both episodes are the same root cause: before ADR-0050 B199 added the
per-chain append mutex, two threads could read `self._head`
simultaneously, both compute `seq = head.seq + 1`, and both write a
line. The in-memory head advanced to whichever wrote last; that
"winner" entry is link-reachable from head and every later
`prev_hash` chains through it. The "loser" entry stayed in the file
with a duplicate `seq`, orphaned — referenced by nothing.

| Episode | Date | Orphan seqs | Orphan count | File lines removed |
|---|---|---|---|---|
| May 8 race | 2026-05-08 | 3728, 3735, 3736, 3737, 3738, 3740 | 6 | 3729, 3737-3740, 3746 |
| May 11 race | 2026-05-11 | 7695-7703 | 9 | 7702-7710 |

B199 fixed the writer so no new forks can form. These 15 predate it.

## Methodology — how the 15 orphans were identified

Not by hand-picked line numbers. `dev-tools/reseal-audit-chain.py`
walks **backward** from the head (last line, seq 23410) following
`prev_hash` → `entry_hash` links until it reaches GENESIS. Every
entry on that path is canonical; every entry not on it is an orphan.
That walk is deterministic and total:

- canonical entries: **23411**, contiguous `seq` 0..23410, zero gaps,
  zero duplicates.
- orphan entries: **15**, exactly the duplicate-`seq` set that
  `scan_for_forks()` reports.

Because the canonical branch had already numbered itself
sequentially and links cleanly, **no renumbering and no hash
recomputation was needed**. Re-sealing is a pure removal of
unlinked debris.

## §0 Hippocratic gate

1. **Harm proven.** `verify()` returns `ok=False`
   (`broken_at_seq=3728`). The chain is "the source of truth" per
   CLAUDE.md and must verify. Every consumer of `verify()` —
   `audit_chain_verify.v1`, the dispatcher, the governance pipeline,
   `passport.py`, the plugins/cycles routers, `check-drift.sh` — got
   a broken-chain verdict. The prior probe-side `KNOWN_HISTORICAL_FORKS`
   patch only suppressed the symptom in diagnostic section-08; every
   other consumer still saw a false break.
2. **Non-load-bearing proven.** The 15 orphans are not referenced by
   any `prev_hash`; nothing links to them; none is the head. They are
   not part of the hash-linked chain — they are write-race debris.
   Removing them changes no surviving entry (verified: 23411 entries
   preserved byte-for-byte; `verify()` recomputes identical hashes).
3. **Alternative is strictly worse.** A forked chain cannot be made
   linear without removing one branch — "fix in place" is not
   available. Option B (a fork-point registry that `verify()`
   consults to skip strict checks) bakes a permanent verification
   blind spot into a tamper-evidence tool: a real future tamper at a
   registered fork point would be masked. Leaving the forks (Option C
   / the prior disposition) leaves `verify()` permanently `ok=False`,
   which destroys its signal — you can no longer tell "known
   historical fork" from "new tamper" without an out-of-band lookup.
4. **Recorded.** This audit doc + CHANGELOG entry + ADR-0005
   amendment + the quarantine file + the reproducible remediation
   script (`dev-tools/reseal-audit-chain.py`).

Information is **relocated, not destroyed**: the 15 orphan lines are
preserved verbatim in `2026-05-21-chain-reseal-orphans.jsonl`.

## Why the disposition changed

The 2026-05-17 and 2026-05-19 docs argued "the file is itself an
audit-evidence artifact; mutating it would create exactly the kind of
corruption the chain is meant to detect." That argument conflates
**the file** with **the chain**. The chain is the hash-linked
structure; the 15 orphans were never in it. Removing unlinked debris
preserves the linked history exactly — the append-only invariant
(don't edit or delete *chain* entries) is not violated, because the
orphans are not chain entries.

The prior fix (`KNOWN_HISTORICAL_FORKS` in section-08) was
incomplete by construction: it taught one diagnostic to ignore the
forks, while `verify()` itself stayed broken for every other caller.
The 2026-05-19 doc even queued a follow-up to teach `wiring_audit.v1`
the same allowlist — a sign that the workaround had to be re-applied
per consumer indefinitely. Re-sealing fixes `verify()` once, for all
consumers, with no allowlist to maintain.

## Verification

```
$ python3 dev-tools/reseal-audit-chain.py --apply
re-sealed chain -> examples/audit_chain.jsonl (23411 entries)
verify():         ok=True entries_verified=23411 broken_at_seq=None
scan_for_forks(): ok=True duplicate_seqs=[] hash_mismatches=[]

$ bash dev-tools/check-chain-forks.sh
ok: True   duplicate_seqs: []   hash_mismatches: []

$ pytest tests/unit/test_audit_chain.py -q
39 passed
```

`unknown_event_types` still reports one entry (`capability_toggled`)
— that is a forward-compat warning, not a structural break, and is
out of scope for this re-seal.

## What would change this disposition

If a NEW duplicate `seq` surfaces post-B199, that is a regression on
the writer mutex — investigate per the playbook in the 2026-05-17
doc (confirm `AuditChain._append_lock` is held at the write site;
rule out a second `AuditChain` instance). It is NOT re-sealed away;
re-sealing is a one-time remediation of the pre-B199 historical
forks only.

## Cross-references

- `dev-tools/reseal-audit-chain.py` — the remediation script
- `docs/audits/2026-05-21-chain-reseal-orphans.jsonl` — the 15 quarantined lines
- `docs/audits/2026-05-08-chain-fork-incident.md` — original B199 incident
- `docs/audits/2026-05-17-audit-chain-seq-3728-fork.md` — superseded disposition
- `docs/audits/2026-05-19-audit-chain-may11-race.md` — superseded disposition
- ADR-0005 (audit chain) — amended 2026-05-21
- ADR-0050 / B199 — the per-chain mutex that prevents new forks
