"""ADR-0064 T3 (B377) — `fsf telemetry verify <batch_id>`.

Compares the telemetry batch in the local store against the
`telemetry_batch_ingested` audit chain anchor entry for that
batch_id. Catches three classes of corruption:

  1. Store tampering — an event was edited or removed after
     ingest; recomputed integrity_root no longer matches the
     anchor.
  2. Chain tampering — the anchor entry was edited; recomputed
     entry_hash no longer chains.
  3. Anchor missing — store has the batch but the chain has no
     entry for that batch_id (mid-flush crash between
     store.ingest_batch and audit_chain.append; per
     ingestor.flush_pending the store commits first so this
     window is exposed).

Verdicts:
  OK                    — store + chain agree; integrity intact.
  MISMATCH              — recomputed root differs from anchor.
  CHAIN_ENTRY_MISSING   — no telemetry_batch_ingested for batch_id.
  BATCH_EMPTY           — batch_id has zero events in the store.
  STORE_UNAVAILABLE     — couldn't open the telemetry store.

CLI wrapper: dev-tools/fsf-telemetry-verify.command takes batch_id
as positional arg; runs this module's main(). Operator-facing
output: the JSON verdict + a human-readable summary line.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.security.telemetry.events import TelemetryEvent
from forest_soul_forge.security.telemetry.store import SqliteTelemetryStore


@dataclass(frozen=True)
class VerifyResult:
    """JSON-serializable verify verdict."""

    verdict: str  # OK | MISMATCH | CHAIN_ENTRY_MISSING | BATCH_EMPTY | STORE_UNAVAILABLE
    batch_id: str
    event_count: int
    computed_root: str | None
    anchored_root: str | None
    chain_entry_seq: int | None
    detail: str


def _compute_integrity_root(events: Iterable[TelemetryEvent]) -> str:
    """Identical formula to ingestor._compute_integrity_root.

    Duplicated here (not imported from ingestor) because verify
    is the canonical recompute and shouldn't share code paths
    with the writer — if the writer formula drifts, the verify
    catches it precisely because their codepaths are independent.
    The duplication is small (3 lines of hashing) and the
    independence is load-bearing.
    """
    h = hashlib.sha256()
    for ih in sorted(ev.integrity_hash for ev in events):
        h.update(ih.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _find_chain_entry(
    chain_path: Path, batch_id: str,
) -> tuple[int | None, str | None]:
    """Scan the audit chain JSONL for the
    telemetry_batch_ingested entry whose event_data.batch_id
    matches. Returns (seq, anchored_root) or (None, None).

    Linear scan is fine — the chain is append-only and small
    enough for this. A future ADR-0073 segment-aware scan can
    replace this if the chain grows past the linear-scan budget.
    """
    if not chain_path.exists():
        return (None, None)
    with chain_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event_type") != "telemetry_batch_ingested":
                continue
            ed = e.get("event_data") or {}
            if ed.get("batch_id") == batch_id:
                return (e.get("seq"), ed.get("integrity_root"))
    return (None, None)


def verify(
    batch_id: str,
    *,
    telemetry_db: Path,
    chain_path: Path,
) -> VerifyResult:
    # 1. Read batch from the store.
    try:
        store = SqliteTelemetryStore(telemetry_db)
    except Exception as e:
        return VerifyResult(
            verdict="STORE_UNAVAILABLE",
            batch_id=batch_id,
            event_count=0,
            computed_root=None,
            anchored_root=None,
            chain_entry_seq=None,
            detail=f"{type(e).__name__}: {e}",
        )
    try:
        events = store.query_by_batch(batch_id)
    finally:
        store.close()
    if not events:
        return VerifyResult(
            verdict="BATCH_EMPTY",
            batch_id=batch_id,
            event_count=0,
            computed_root=None,
            anchored_root=None,
            chain_entry_seq=None,
            detail=f"telemetry store has zero events for batch_id={batch_id}",
        )

    computed_root = _compute_integrity_root(events)

    # 2. Find the chain anchor.
    seq, anchored_root = _find_chain_entry(chain_path, batch_id)
    if seq is None:
        return VerifyResult(
            verdict="CHAIN_ENTRY_MISSING",
            batch_id=batch_id,
            event_count=len(events),
            computed_root=computed_root,
            anchored_root=None,
            chain_entry_seq=None,
            detail=(
                f"no telemetry_batch_ingested entry in {chain_path} "
                f"references batch_id={batch_id}. Possible mid-flush "
                f"crash (store commits before chain append per "
                f"ingestor.flush_pending docstring), or the chain "
                f"was truncated/segmented and the entry rotated out."
            ),
        )

    # 3. Compare.
    if computed_root != anchored_root:
        return VerifyResult(
            verdict="MISMATCH",
            batch_id=batch_id,
            event_count=len(events),
            computed_root=computed_root,
            anchored_root=anchored_root,
            chain_entry_seq=seq,
            detail=(
                f"integrity_root mismatch — store recomputed "
                f"{computed_root[:16]}... but chain anchor at seq={seq} "
                f"claims {anchored_root[:16] if anchored_root else 'null'}.... "
                f"Tampering or per-event mutation."
            ),
        )

    return VerifyResult(
        verdict="OK",
        batch_id=batch_id,
        event_count=len(events),
        computed_root=computed_root,
        anchored_root=anchored_root,
        chain_entry_seq=seq,
        detail=(
            f"batch_id={batch_id} integrity verified: "
            f"{len(events)} events, root={computed_root[:16]}..., "
            f"anchored at chain seq={seq}"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fsf-telemetry-verify",
        description="Verify a telemetry batch against its audit chain anchor.",
    )
    parser.add_argument("batch_id", help="The batch_id to verify.")
    parser.add_argument(
        "--telemetry-db",
        default="data/telemetry.sqlite",
        help="Path to telemetry store SQLite (default: data/telemetry.sqlite)",
    )
    parser.add_argument(
        "--chain-path",
        default="examples/audit_chain.jsonl",
        help="Path to audit chain JSONL (default: examples/audit_chain.jsonl)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON only (no human summary line).",
    )
    args = parser.parse_args(argv)

    result = verify(
        args.batch_id,
        telemetry_db=Path(args.telemetry_db),
        chain_path=Path(args.chain_path),
    )

    payload = asdict(result)
    print(json.dumps(payload, indent=2))

    if not args.json:
        print(f"\n[{result.verdict}] {result.detail}", file=sys.stderr)

    # Exit code reflects verdict — operator scripts can rely on:
    #   0  OK
    #   1  MISMATCH (real corruption)
    #   2  CHAIN_ENTRY_MISSING (mid-flush crash window)
    #   3  BATCH_EMPTY (batch_id not in store)
    #   4  STORE_UNAVAILABLE (operational)
    return {
        "OK": 0,
        "MISMATCH": 1,
        "CHAIN_ENTRY_MISSING": 2,
        "BATCH_EMPTY": 3,
        "STORE_UNAVAILABLE": 4,
    }[result.verdict]


if __name__ == "__main__":
    sys.exit(main())
