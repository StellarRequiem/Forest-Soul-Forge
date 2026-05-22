#!/usr/bin/env python3
"""Re-seal the audit chain by removing pre-B199 write-race orphan entries.

Background
----------
Before ADR-0050 B199 introduced the per-chain append mutex, two threads
could read ``self._head`` simultaneously, both compute ``seq = head.seq + 1``,
and both write a line. The first writer's entry got physically persisted but
*orphaned* — the in-memory head advanced to the second writer's entry, so
every subsequent ``prev_hash`` linked through the winner. The loser's line
stayed in the file with a duplicate ``seq``, referenced by nothing.

Two such episodes are baked into ``examples/audit_chain.jsonl``:
the 2026-05-08 race (6 orphans near seq 3728) and the 2026-05-11 race
(9 orphans at seqs 7695-7703). ``AuditChain.verify()`` walks the file
line-by-line and trips on the first duplicate seq, so it reports the
whole chain as broken even though the *linked* history is intact.

What this script does
---------------------
1. Loads every line of the chain.
2. Walks BACKWARD from the head (last line) following ``prev_hash`` ->
   ``entry_hash`` links until it reaches GENESIS. Every entry on that
   path is *canonical*; every entry not on it is an *orphan*.
3. Quarantines the orphan lines verbatim to a sibling file (nothing is
   destroyed — the bytes are relocated out of the linked chain).
4. Rewrites the chain with only the canonical entries, in file order.

Because the orphans are dead-end branches and the canonical branch
already numbered itself sequentially, NO renumbering and NO hash
recomputation is needed: every surviving entry keeps its exact seq,
prev_hash, entry_hash, timestamp, and signature. Re-sealing is a pure
removal of unlinked debris, not a rewrite of history.

The script is idempotent — run against an already-clean chain it finds
zero orphans and changes nothing.

Usage
-----
  python3 dev-tools/reseal-audit-chain.py                 # dry run
  python3 dev-tools/reseal-audit-chain.py --apply         # re-seal in place
  python3 dev-tools/reseal-audit-chain.py --apply \\
      --chain path/to/chain.jsonl --quarantine path/to/orphans.jsonl

Exit codes: 0 = clean / re-sealed OK, 1 = post-reseal verification failed.

See docs/audits/2026-05-21-chain-fork-reseal.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHAIN = REPO_ROOT / "examples" / "audit_chain.jsonl"
DEFAULT_QUARANTINE = REPO_ROOT / "docs" / "audits" / "2026-05-21-chain-reseal-orphans.jsonl"


def find_orphans(raw_lines: list[str]) -> tuple[list[int], list[int]]:
    """Return (canonical_indices, orphan_indices) into the non-blank entry list.

    Indices are positions in the list of non-blank entries, in file order.
    Canonical = reachable by a backward prev_hash walk from the last entry.
    """
    entries = []  # (file_lineno_1based, parsed_obj)
    for i, line in enumerate(raw_lines):
        s = line.strip()
        if not s:
            continue
        entries.append((i + 1, json.loads(s)))

    if not entries:
        return [], []

    # entry_hash -> first index that produced it
    hash_to_idx: dict[str, int] = {}
    for idx, (_, obj) in enumerate(entries):
        hash_to_idx.setdefault(obj["entry_hash"], idx)

    canonical: set[int] = set()
    cur = len(entries) - 1  # head = last line
    while True:
        canonical.add(cur)
        obj = entries[cur][1]
        if obj["prev_hash"] == "GENESIS":
            break
        nxt = hash_to_idx.get(obj["prev_hash"])
        if nxt is None:
            ln = entries[cur][0]
            raise SystemExit(
                f"dead end: line {ln} (seq {obj['seq']}) prev_hash "
                f"{obj['prev_hash'][:12]} not found — chain has a real "
                f"break, not just write-race forks; aborting."
            )
        cur = nxt

    canonical_idx = sorted(canonical)
    orphan_idx = [i for i in range(len(entries)) if i not in canonical]
    return canonical_idx, orphan_idx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chain", type=Path, default=DEFAULT_CHAIN)
    ap.add_argument("--quarantine", type=Path, default=DEFAULT_QUARANTINE)
    ap.add_argument("--apply", action="store_true",
                    help="write the re-sealed chain (default is dry run)")
    args = ap.parse_args()

    raw_lines = args.chain.read_text(encoding="utf-8").splitlines(keepends=True)
    entries = [(i + 1, ln) for i, ln in enumerate(raw_lines) if ln.strip()]
    canonical_idx, orphan_idx = find_orphans(raw_lines)

    print(f"chain:            {args.chain}")
    print(f"entries (lines):  {len(entries)}")
    print(f"canonical:        {len(canonical_idx)}")
    print(f"orphans:          {len(orphan_idx)}")
    for i in orphan_idx:
        file_ln, raw = entries[i]
        obj = json.loads(raw)
        print(f"  orphan line {file_ln:>6}  seq={obj['seq']:<6} "
              f"{obj['event_type']:<30} hash={obj['entry_hash'][:12]}")

    if not orphan_idx:
        print("chain is already clean — nothing to re-seal.")
        return 0

    if not args.apply:
        print("\ndry run — pass --apply to re-seal.")
        return 0

    # Quarantine orphan lines verbatim (relocate, don't destroy).
    quarantine_lines = [entries[i][1] for i in orphan_idx]
    args.quarantine.write_text("".join(quarantine_lines), encoding="utf-8")
    print(f"\nquarantined {len(quarantine_lines)} orphan lines -> {args.quarantine}")

    # Rewrite the chain with canonical entries only, in file order.
    keep_lines = [entries[i][1] for i in canonical_idx]
    tmp = args.chain.with_suffix(args.chain.suffix + ".reseal.tmp")
    tmp.write_text("".join(keep_lines), encoding="utf-8")
    tmp.replace(args.chain)
    print(f"re-sealed chain    -> {args.chain} ({len(keep_lines)} entries)")

    # Verify the result with the real verifier + fork scanner.
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from forest_soul_forge.core.audit_chain import AuditChain

    chain = AuditChain(args.chain)
    vr = chain.verify()
    sr = chain.scan_for_forks()
    print(f"\nverify():          ok={vr.ok} entries_verified={vr.entries_verified} "
          f"broken_at_seq={vr.broken_at_seq}")
    print(f"scan_for_forks():  ok={sr.ok} duplicate_seqs={list(sr.duplicate_seqs)} "
          f"hash_mismatches={list(sr.hash_mismatches)}")
    if vr.ok and sr.ok:
        print("re-seal verified — chain links cleanly genesis -> head.")
        return 0
    print("POST-RESEAL VERIFICATION FAILED — investigate before committing.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
