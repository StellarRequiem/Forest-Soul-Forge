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
