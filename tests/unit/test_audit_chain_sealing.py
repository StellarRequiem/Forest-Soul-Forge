"""ADR-0073 T2 (B300) — audit segment sealing flow tests.

Pure-function tests against the seal_segment() runner. The B291
substrate (SegmentMeta + SegmentIndex + merkle_root +
AnchorPayload) is the data layer; this test set proves the runner
that consumes it produces the right new-index + anchor shape.

Test fixtures use a tmp_path directory + hand-crafted segment
files with known entry_hash/seq values, so the Merkle root is
deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain_segments import (
    AnchorPayload,
    SealError,
    SealOutcome,
    SegmentIndex,
    SegmentMeta,
    merkle_root,
    seal_segment,
)


def _write_segment(path: Path, entries: list[dict]) -> None:
    """Write a list of entry dicts to a segment file, one per line."""
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _tail_index(file_name: str, month: str, seq_start: int = 0) -> SegmentIndex:
    """Build a one-segment index with the named file as its
    unsealed tail."""
    return SegmentIndex(
        schema_version=1,
        segments=(
            SegmentMeta(
                seq_start=seq_start,
                seq_end=None,
                file=file_name,
                month=month,
                sealed=False,
                merkle_root=None,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_seal_segment_marks_tail_sealed_with_merkle_root(tmp_path):
    """Sealing a tail with 3 entries produces a sealed segment with
    seq_end = last seq and merkle_root computed over all entry hashes."""
    seg_file = "audit_chain_2026-05.jsonl"
    seg_path = tmp_path / seg_file
    entries = [
        {"seq": 10, "entry_hash": "a" * 64},
        {"seq": 11, "entry_hash": "b" * 64},
        {"seq": 12, "entry_hash": "c" * 64},
    ]
    _write_segment(seg_path, entries)
    idx = _tail_index(seg_file, "2026-05", seq_start=10)

    outcome = seal_segment(
        index=idx, segment_dir=tmp_path, next_month="2026-06",
    )

    sealed = outcome.new_index.segments[0]
    assert sealed.sealed is True
    assert sealed.seq_end == 12
    assert sealed.merkle_root == merkle_root(["a" * 64, "b" * 64, "c" * 64])
    assert sealed.file == seg_file  # file name preserved


def test_seal_segment_allocates_new_tail_for_next_month(tmp_path):
    """The returned new_index includes a freshly-allocated tail
    segment for next_month with the right seq_start + filename."""
    seg_file = "audit_chain_2026-05.jsonl"
    seg_path = tmp_path / seg_file
    _write_segment(seg_path, [{"seq": 42, "entry_hash": "x" * 64}])
    idx = _tail_index(seg_file, "2026-05", seq_start=42)

    outcome = seal_segment(
        index=idx, segment_dir=tmp_path, next_month="2026-06",
    )

    assert len(outcome.new_index.segments) == 2
    new_tail = outcome.new_index.segments[1]
    assert new_tail.sealed is False
    assert new_tail.merkle_root is None
    assert new_tail.seq_start == 43  # seq_end + 1
    assert new_tail.seq_end is None
    assert new_tail.file == "audit_chain_2026-06.jsonl"
    assert new_tail.month == "2026-06"


def test_seal_segment_returns_anchor_payload_matching_sealed_segment(tmp_path):
    """The AnchorPayload field-for-field matches what gets written
    to the new sealed segment. Caller emits this as the
    audit_chain_anchor event_data."""
    seg_file = "audit_chain_2026-05.jsonl"
    seg_path = tmp_path / seg_file
    entries = [
        {"seq": 100, "entry_hash": "1" * 64},
        {"seq": 101, "entry_hash": "2" * 64},
    ]
    _write_segment(seg_path, entries)
    idx = _tail_index(seg_file, "2026-05", seq_start=100)

    outcome = seal_segment(
        index=idx, segment_dir=tmp_path, next_month="2026-06",
    )

    anchor = outcome.anchor
    assert isinstance(anchor, AnchorPayload)
    assert anchor.prior_segment_file == seg_file
    assert anchor.prior_seq_end == 101
    assert anchor.prior_merkle_root == merkle_root(["1" * 64, "2" * 64])
    assert anchor.prior_segment_entry_count == 2


def test_seal_segment_returns_next_segment_path_pointing_at_new_tail(tmp_path):
    """next_segment_path is the file the caller writes the anchor
    entry into — must point at the NEW tail, not the sealed one."""
    seg_file = "audit_chain_2026-05.jsonl"
    _write_segment(tmp_path / seg_file, [{"seq": 0, "entry_hash": "f" * 64}])
    idx = _tail_index(seg_file, "2026-05")

    outcome = seal_segment(
        index=idx, segment_dir=tmp_path, next_month="2026-06",
    )

    assert outcome.next_segment_path == tmp_path / "audit_chain_2026-06.jsonl"


def test_seal_segment_preserves_already_sealed_segments(tmp_path):
    """A multi-segment index with prior sealed segments keeps them
    intact through a seal pass — only the tail changes state."""
    # Existing sealed segment.
    prior = SegmentMeta(
        seq_start=0, seq_end=99, file="audit_chain_2026-04.jsonl",
        month="2026-04", sealed=True, merkle_root="prior_root",
    )
    # Active tail.
    tail_file = "audit_chain_2026-05.jsonl"
    _write_segment(tmp_path / tail_file, [{"seq": 100, "entry_hash": "z" * 64}])
    tail = SegmentMeta(
        seq_start=100, seq_end=None, file=tail_file,
        month="2026-05", sealed=False, merkle_root=None,
    )
    idx = SegmentIndex(schema_version=1, segments=(prior, tail))

    outcome = seal_segment(
        index=idx, segment_dir=tmp_path, next_month="2026-06",
    )

    # Three segments in the new index: prior unchanged, tail sealed,
    # new tail appended.
    assert len(outcome.new_index.segments) == 3
    assert outcome.new_index.segments[0] == prior
    assert outcome.new_index.segments[1].sealed is True
    assert outcome.new_index.segments[1].file == tail_file
    assert outcome.new_index.segments[2].sealed is False
    assert outcome.new_index.segments[2].seq_start == 101


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_seal_segment_raises_when_no_tail(tmp_path):
    """An index with no unsealed segment can't be sealed — that's
    the T4 migration helper's job."""
    idx = SegmentIndex(schema_version=1, segments=())
    with pytest.raises(SealError, match="no current"):
        seal_segment(index=idx, segment_dir=tmp_path)


def test_seal_segment_raises_when_tail_file_missing(tmp_path):
    """Tail metadata exists but the file isn't on disk — refuse to
    seal rather than producing an empty Merkle root."""
    idx = _tail_index("nonexistent.jsonl", "2026-05")
    with pytest.raises(SealError, match="missing"):
        seal_segment(index=idx, segment_dir=tmp_path)


def test_seal_segment_raises_on_malformed_entry_line(tmp_path):
    """A line that doesn't parse JSON corrupts the Merkle pass —
    refuse to seal rather than silently dropping the row."""
    seg_file = "audit_chain_bad.jsonl"
    seg_path = tmp_path / seg_file
    seg_path.write_text("not_json\n", encoding="utf-8")
    idx = _tail_index(seg_file, "2026-05")
    with pytest.raises(SealError, match="malformed JSON"):
        seal_segment(index=idx, segment_dir=tmp_path)


def test_seal_segment_raises_on_missing_entry_hash(tmp_path):
    """An entry without entry_hash can't contribute to the Merkle
    pass — same refusal posture."""
    seg_file = "audit_chain_missing.jsonl"
    seg_path = tmp_path / seg_file
    seg_path.write_text(
        json.dumps({"seq": 1, "event_type": "x"}) + "\n",  # no entry_hash
        encoding="utf-8",
    )
    idx = _tail_index(seg_file, "2026-05")
    with pytest.raises(SealError, match="missing entry_hash"):
        seal_segment(index=idx, segment_dir=tmp_path)


def test_seal_segment_raises_on_empty_tail_file(tmp_path):
    """An empty tail file has nothing to seal — refuse rather than
    producing a sentinel sha256("")."""
    seg_file = "audit_chain_empty.jsonl"
    (tmp_path / seg_file).touch()
    idx = _tail_index(seg_file, "2026-05")
    with pytest.raises(SealError, match="empty"):
        seal_segment(index=idx, segment_dir=tmp_path)
