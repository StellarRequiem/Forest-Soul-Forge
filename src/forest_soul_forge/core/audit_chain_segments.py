"""Audit chain segmentation substrate — ADR-0073 T1 (B291).

Reads + writes the segment index and provides a SegmentReader that
lazy-loads sealed segments. T1 ships the data layer + reader; T2
ships the sealing flow that promotes the tail segment to sealed +
emits the anchor entry.

## Surface

  - :class:`SegmentMeta` — one entry in the index
  - :class:`SegmentIndex` — full index file contents
  - :class:`AnchorPayload` — shape of audit_chain_anchor event_data
  - :func:`load_segment_index(path=None)` — read + validate
  - :func:`save_segment_index(index, path=None)` — atomic write
  - :func:`current_segment_month()` — UTC year-month string
  - :func:`merkle_root(entry_hashes)` — Merkle root helper used by
    sealing

## Why pure-function

T1 ships index data layer + Merkle helper only. The sealing flow
(T2), the verify_chain extension (T3), and the migration helper
(T4) all consume this. Keeping T1 pure-function makes them
testable in isolation against fake indexes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


DEFAULT_INDEX_PATH = Path("examples/audit_chain_index.json")
DEFAULT_SEGMENT_DIR = Path("examples")

ENV_INDEX = "FSF_AUDIT_CHAIN_INDEX_PATH"
ENV_SEGMENT_DIR = "FSF_AUDIT_CHAIN_SEGMENT_DIR"

SCHEMA_VERSION = 1


class SegmentIndexError(RuntimeError):
    """Raised on hard-fatal index problems (top-level not a mapping,
    schema_version mismatch, file missing when expected). Per-entry
    issues surface as soft errors."""


@dataclass(frozen=True)
class SegmentMeta:
    """One entry in the segment index.

    seq_end is None for the current (tail) segment — it grows as
    entries append. seq_end gets set at sealing time (T2). merkle_root
    is computed at sealing and stays immutable thereafter.
    """
    seq_start: int
    seq_end: Optional[int]
    file: str
    month: str  # YYYY-MM
    sealed: bool
    merkle_root: Optional[str] = None  # set at sealing


@dataclass(frozen=True)
class SegmentIndex:
    """Full audit_chain_index.json contents."""
    schema_version: int
    segments: tuple[SegmentMeta, ...]

    def current(self) -> Optional[SegmentMeta]:
        """Return the tail (sealed=False) segment, or None when the
        chain hasn't been bootstrapped to segments yet."""
        for s in self.segments:
            if not s.sealed:
                return s
        return None

    def for_seq(self, seq: int) -> Optional[SegmentMeta]:
        """Return the segment containing ``seq``, or None when seq
        is past the current tail."""
        for s in self.segments:
            if seq < s.seq_start:
                continue
            if s.seq_end is None or seq <= s.seq_end:
                return s
        return None

    def sealed_segments(self) -> tuple[SegmentMeta, ...]:
        return tuple(s for s in self.segments if s.sealed)


@dataclass(frozen=True)
class AnchorPayload:
    """Shape of an audit_chain_anchor event's event_data.

    Operator-readable provenance for the seal: which segment got
    frozen, where its last entry sits, the Merkle root over its
    entry hashes (used by mode=tail verifiers to skip walking),
    and the count for sanity-check.
    """
    prior_segment_file: str
    prior_seq_end: int
    prior_merkle_root: str
    prior_segment_entry_count: int


# ---------------------------------------------------------------------------
# Index loader / writer
# ---------------------------------------------------------------------------


def load_segment_index(
    path: Optional[Path] = None,
) -> SegmentIndex:
    """Read + validate the audit chain segment index.

    Missing file is benign: returns an empty SegmentIndex (no
    segments). The migration helper (T4) creates the index from
    the existing monolithic chain. Callers that need a non-empty
    index check for it explicitly.

    Raises :class:`SegmentIndexError` on structural failures
    (malformed JSON, schema mismatch, top-level not an object).
    """
    import os as _os
    resolved = (
        path if path is not None
        else Path(_os.environ.get(ENV_INDEX, str(DEFAULT_INDEX_PATH)))
    )

    if not resolved.exists():
        return SegmentIndex(
            schema_version=SCHEMA_VERSION,
            segments=(),
        )

    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as e:
        raise SegmentIndexError(f"{resolved}: read failed: {e}") from e

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise SegmentIndexError(
            f"{resolved}: malformed JSON: {e}"
        ) from e

    if not isinstance(raw, dict):
        raise SegmentIndexError(
            f"{resolved}: top-level must be a JSON object"
        )

    sv = raw.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise SegmentIndexError(
            f"{resolved}: schema_version {sv!r} not supported "
            f"(expected {SCHEMA_VERSION})"
        )

    raw_segments = raw.get("segments")
    if not isinstance(raw_segments, list):
        raise SegmentIndexError(
            f"{resolved}: 'segments' must be a list"
        )

    segs: list[SegmentMeta] = []
    for idx, raw_seg in enumerate(raw_segments):
        if not isinstance(raw_seg, dict):
            raise SegmentIndexError(
                f"{resolved}: segments[{idx}] must be a mapping"
            )
        required = {"seq_start", "file", "month", "sealed"}
        missing = required - set(raw_seg.keys())
        if missing:
            raise SegmentIndexError(
                f"{resolved}: segments[{idx}] missing required "
                f"fields: {sorted(missing)}"
            )
        segs.append(SegmentMeta(
            seq_start=int(raw_seg["seq_start"]),
            seq_end=(
                int(raw_seg["seq_end"])
                if raw_seg.get("seq_end") is not None else None
            ),
            file=str(raw_seg["file"]),
            month=str(raw_seg["month"]),
            sealed=bool(raw_seg["sealed"]),
            merkle_root=raw_seg.get("merkle_root"),
        ))

    return SegmentIndex(
        schema_version=int(sv),
        segments=tuple(segs),
    )


def save_segment_index(
    index: SegmentIndex,
    path: Optional[Path] = None,
) -> Path:
    """Atomic write of the segment index. Writes to <path>.tmp then
    renames over the target."""
    p = path if path is not None else DEFAULT_INDEX_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": index.schema_version,
        "segments": [
            {
                "seq_start":     s.seq_start,
                "seq_end":       s.seq_end,
                "file":          s.file,
                "month":         s.month,
                "sealed":        s.sealed,
                **(
                    {"merkle_root": s.merkle_root}
                    if s.merkle_root is not None else {}
                ),
            }
            for s in index.segments
        ],
    }
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(p)
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def current_segment_month() -> str:
    """UTC year-month for the current tail segment (e.g. '2026-05')."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def segment_filename_for_month(month: str) -> str:
    """Filename convention for a month: audit_chain_YYYY-MM.jsonl."""
    return f"audit_chain_{month}.jsonl"


def merkle_root(entry_hashes: list[str]) -> str:
    """Compute Merkle root over a list of entry hashes.

    Standard binary Merkle: pair adjacent hashes, sha256 the
    concatenation, repeat. Odd levels duplicate the last hash to
    pair. Used by T2 sealing to summarize a segment in one hash.

    The root lets a mode=tail verifier (ADR-0073 D3) skip walking
    the sealed segment — the operator trusts the anchor's
    merkle_root field rather than re-hashing every line.
    """
    if not entry_hashes:
        # Empty Merkle is conventional: sha256 of empty string.
        return hashlib.sha256(b"").hexdigest()

    level = [bytes.fromhex(h) for h in entry_hashes]
    while len(level) > 1:
        next_level: list[bytes] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            next_level.append(
                hashlib.sha256(left + right).digest()
            )
        level = next_level
    return level[0].hex()


def append_segment_entry(
    segment_path: Path,
    entry_json: str,
) -> None:
    """Append one entry line to a segment file.

    Pure file-append; no index update (the index gets touched
    only at seal time). The caller is responsible for keeping
    the index's seq_start invariant — for the current tail
    segment this happens implicitly because we just append.

    Atomic enough: O_APPEND on POSIX guarantees no two writers
    interleave their lines, AND the daemon's write_lock already
    serializes audit appends. So this is a thin wrapper that
    documents the seam.
    """
    with segment_path.open("a", encoding="utf-8") as f:
        f.write(entry_json)
        if not entry_json.endswith("\n"):
            f.write("\n")


# ---------------------------------------------------------------------------
# ADR-0073 T2 (B300) — sealing flow
# ---------------------------------------------------------------------------


class SealError(RuntimeError):
    """Raised when the sealing flow can't proceed (no tail segment,
    tail file missing, malformed entry hashes). The audit chain stays
    untouched on SealError — sealing is best-effort observability,
    not a correctness substrate."""


def _read_segment_hashes_and_seqs(
    segment_path: Path,
) -> tuple[list[str], list[int]]:
    """Scan a segment file; return (entry_hashes, seqs) in order.

    Each line is one JSON entry. We extract ``entry_hash`` and
    ``seq`` only — full parse isn't needed for the Merkle pass.
    Lines that don't parse or are missing either field raise
    SealError; a malformed sealed segment would corrupt the anchor
    semantics so we refuse to seal on any error rather than
    silently dropping rows.
    """
    if not segment_path.exists():
        raise SealError(f"segment file missing: {segment_path}")
    hashes: list[str] = []
    seqs: list[int] = []
    with segment_path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise SealError(
                    f"{segment_path}:{lineno}: malformed JSON: {e}"
                ) from e
            h = obj.get("entry_hash")
            s = obj.get("seq")
            if not isinstance(h, str) or not isinstance(s, int):
                raise SealError(
                    f"{segment_path}:{lineno}: missing entry_hash/seq"
                )
            hashes.append(h)
            seqs.append(s)
    if not hashes:
        raise SealError(f"segment file empty: {segment_path}")
    return hashes, seqs


@dataclass(frozen=True)
class SealOutcome:
    """Result of one seal pass. The caller (the runner) takes the
    new_index and writes it to disk, then appends the anchor entry
    to the new tail segment's file via the normal audit chain
    surface.

    Splitting the side-effects out of seal_segment() keeps the
    function testable without a live audit chain — the test builds
    a fake segment file + index, calls seal_segment, and inspects
    the returned outcome.
    """
    new_index: SegmentIndex
    anchor: AnchorPayload
    next_segment_path: Path  # caller writes the anchor entry here


def seal_segment(
    *,
    index: SegmentIndex,
    segment_dir: Path,
    next_month: Optional[str] = None,
) -> SealOutcome:
    """Seal the current tail segment and return the new index + anchor.

    Steps:

    1. Identify the current tail segment from ``index.current()``.
       SealError if there isn't one (chain not yet bootstrapped to
       segments — that's the T4 migration helper's job).
    2. Read the tail's file, extract per-entry (entry_hash, seq).
    3. Compute merkle_root over the hashes.
    4. Build the sealed SegmentMeta (seq_end = last seq).
    5. Build the new tail SegmentMeta for ``next_month``
       (defaults to current UTC year-month if not supplied;
       caller-overridable for tests).
    6. Build the AnchorPayload.
    7. Return SealOutcome with the new index (sealed + new tail
       both present), the anchor payload, and the path the caller
       should write the anchor entry to (the NEW tail segment —
       the anchor entry belongs in the post-seal segment because
       it documents what happened before).

    Pure function: doesn't write anything to disk. The runner
    consumes SealOutcome to drive the disk side-effects. That
    keeps T2 testable without a live chain.
    """
    tail = index.current()
    if tail is None:
        raise SealError(
            "no current (unsealed) segment to seal; "
            "run the T4 migration helper first"
        )

    segment_path = segment_dir / tail.file
    hashes, seqs = _read_segment_hashes_and_seqs(segment_path)

    root = merkle_root(hashes)
    seq_end = seqs[-1]
    entry_count = len(hashes)

    sealed_meta = SegmentMeta(
        seq_start=tail.seq_start,
        seq_end=seq_end,
        file=tail.file,
        month=tail.month,
        sealed=True,
        merkle_root=root,
    )

    chosen_month = next_month or current_segment_month()
    new_tail_meta = SegmentMeta(
        seq_start=seq_end + 1,
        seq_end=None,
        file=segment_filename_for_month(chosen_month),
        month=chosen_month,
        sealed=False,
        merkle_root=None,
    )

    # Replace the tail in-place; preserve every other (already-sealed)
    # segment in original order. Then append the new tail.
    new_segments = tuple(
        sealed_meta if s.file == tail.file and not s.sealed else s
        for s in index.segments
    ) + (new_tail_meta,)

    new_index = SegmentIndex(
        schema_version=index.schema_version,
        segments=new_segments,
    )

    anchor = AnchorPayload(
        prior_segment_file=tail.file,
        prior_seq_end=seq_end,
        prior_merkle_root=root,
        prior_segment_entry_count=entry_count,
    )

    next_segment_path = segment_dir / new_tail_meta.file
    return SealOutcome(
        new_index=new_index,
        anchor=anchor,
        next_segment_path=next_segment_path,
    )


# ---------------------------------------------------------------------------
# ADR-0073 T3a (B301) — sealed-segment Merkle verifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentVerifyIssue:
    """One issue surfaced by ``verify_sealed_segments``.

    ``kind`` is a stable string for programmatic dispatch in callers
    (the future verify_chain mode='tail' integration, dashboards,
    operator runbooks). ``segment_file`` always points at the
    affected file; ``details`` is human-readable.
    """
    kind: str   # "merkle_mismatch" | "file_missing" | "no_root" | "scan_error"
    segment_file: str
    details: str


@dataclass(frozen=True)
class SegmentVerifyResult:
    """Aggregate outcome of a sealed-segment verification pass.

    ``ok`` is True iff every sealed segment hashes to its stored
    merkle_root. ``segments_verified`` counts segments that hashed
    clean (excludes ones with issues). Issues are surfaced as a
    tuple so the operator sees ALL problems in one pass instead of
    bailing on the first.
    """
    ok: bool
    segments_verified: int
    issues: tuple[SegmentVerifyIssue, ...]


def verify_sealed_segments(
    *,
    index: SegmentIndex,
    segment_dir: Path,
) -> SegmentVerifyResult:
    """Verify each sealed segment's Merkle root against its file.

    For every segment in the index with ``sealed=True``:

      1. Read the segment file; extract entry_hash from each entry.
      2. Compute Merkle root via :func:`merkle_root`.
      3. Compare to the segment's stored ``merkle_root``.

    A mismatch is the tamper signal — somebody edited the file
    after seal time. The operator's response is to consult the
    on-chain ``audit_chain_anchor`` event for the segment (the
    anchor carries the same Merkle root, signed if ADR-0049 is
    active) and treat the disk file as suspect.

    This function is the building block for the full verify_chain
    mode='tail' integration (T3b, queued): once an operator
    confirms every sealed segment hashes clean, the line-by-line
    verifier can skip those entries and only walk the tail. T3a
    here ships the substrate without touching the existing
    AuditChain.verify() to keep the diff focused.

    Errors don't raise — they accumulate as issues. Callers that
    want to refuse on any issue check ``result.ok``. Returning
    issues by value lets dashboards / runbooks show "5 segments
    sealed, 1 mismatch on 2026-03" without re-running.

    A segment marked sealed but missing ``merkle_root`` is a
    schema violation; surfaces as ``kind="no_root"`` so the
    operator can chase the index-corruption case separately from
    actual tamper.
    """
    issues: list[SegmentVerifyIssue] = []
    verified = 0

    for seg in index.sealed_segments():
        if seg.merkle_root is None:
            issues.append(SegmentVerifyIssue(
                kind="no_root",
                segment_file=seg.file,
                details=(
                    "segment marked sealed but missing merkle_root "
                    "in the index"
                ),
            ))
            continue

        seg_path = segment_dir / seg.file
        try:
            hashes, _seqs = _read_segment_hashes_and_seqs(seg_path)
        except SealError as e:
            # _read_segment_hashes_and_seqs uses "missing"/"empty"/
            # "malformed JSON"/"missing entry_hash" in its messages.
            # Classify the first two as file_missing, the rest as
            # scan_error so dashboards can split presentation.
            msg = str(e)
            if "missing" in msg and seg.file in msg:
                kind = "file_missing"
            else:
                kind = "scan_error"
            issues.append(SegmentVerifyIssue(
                kind=kind,
                segment_file=seg.file,
                details=msg,
            ))
            continue

        recomputed = merkle_root(hashes)
        if recomputed != seg.merkle_root:
            issues.append(SegmentVerifyIssue(
                kind="merkle_mismatch",
                segment_file=seg.file,
                details=(
                    f"stored root {seg.merkle_root[:16]}… vs "
                    f"recomputed {recomputed[:16]}… "
                    f"over {len(hashes)} entries"
                ),
            ))
            continue

        verified += 1

    return SegmentVerifyResult(
        ok=not issues,
        segments_verified=verified,
        issues=tuple(issues),
    )
