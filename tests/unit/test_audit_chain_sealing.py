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


# ---------------------------------------------------------------------------
# ADR-0073 T3a (B301) — verify_sealed_segments
# ---------------------------------------------------------------------------

from forest_soul_forge.core.audit_chain_segments import (
    SegmentVerifyIssue,
    SegmentVerifyResult,
    verify_sealed_segments,
)


def _sealed_index(file_name: str, entry_hashes: list[str],
                  month: str = "2026-05", seq_start: int = 0,
                  override_root: str | None = None) -> SegmentIndex:
    """Build a one-segment sealed index with the Merkle root
    derived from `entry_hashes` (or `override_root` to simulate
    a tamper situation where the stored root doesn't match the
    file)."""
    root = override_root if override_root is not None else merkle_root(entry_hashes)
    seq_end = seq_start + len(entry_hashes) - 1
    return SegmentIndex(
        schema_version=1,
        segments=(
            SegmentMeta(
                seq_start=seq_start,
                seq_end=seq_end,
                file=file_name,
                month=month,
                sealed=True,
                merkle_root=root,
            ),
        ),
    )


def test_verify_sealed_segments_clean(tmp_path):
    """A sealed segment whose file hashes to the stored root verifies."""
    file = "audit_chain_2026-05.jsonl"
    entries = [
        {"seq": 0, "entry_hash": "a" * 64},
        {"seq": 1, "entry_hash": "b" * 64},
    ]
    _write_segment(tmp_path / file, entries)
    idx = _sealed_index(file, ["a" * 64, "b" * 64])

    r = verify_sealed_segments(index=idx, segment_dir=tmp_path)
    assert isinstance(r, SegmentVerifyResult)
    assert r.ok is True
    assert r.segments_verified == 1
    assert r.issues == ()


def test_verify_sealed_segments_detects_merkle_mismatch(tmp_path):
    """A segment whose file hashes differently from the stored root
    surfaces as kind='merkle_mismatch' — the tamper signal."""
    file = "audit_chain_2026-05.jsonl"
    entries = [
        {"seq": 0, "entry_hash": "a" * 64},
        {"seq": 1, "entry_hash": "c" * 64},  # not what the stored root expects
    ]
    _write_segment(tmp_path / file, entries)
    idx = _sealed_index(
        file, ["a" * 64, "b" * 64],  # stored root computed from these
    )

    r = verify_sealed_segments(index=idx, segment_dir=tmp_path)
    assert r.ok is False
    assert len(r.issues) == 1
    assert r.issues[0].kind == "merkle_mismatch"
    assert r.issues[0].segment_file == file


def test_verify_sealed_segments_detects_missing_file(tmp_path):
    """A sealed segment whose file isn't on disk surfaces as
    kind='file_missing' — operator can restore from backup."""
    idx = _sealed_index("nonexistent.jsonl", ["a" * 64])
    r = verify_sealed_segments(index=idx, segment_dir=tmp_path)
    assert r.ok is False
    assert r.issues[0].kind == "file_missing"


def test_verify_sealed_segments_detects_no_root(tmp_path):
    """A segment marked sealed but missing merkle_root in the index
    is a schema violation; surfaces as kind='no_root' so the operator
    chases it separately from actual tamper."""
    idx = SegmentIndex(schema_version=1, segments=(
        SegmentMeta(
            seq_start=0, seq_end=0, file="x.jsonl",
            month="2026-05", sealed=True, merkle_root=None,
        ),
    ))
    r = verify_sealed_segments(index=idx, segment_dir=tmp_path)
    assert r.ok is False
    assert r.issues[0].kind == "no_root"


def test_verify_sealed_segments_skips_unsealed(tmp_path):
    """Unsealed (tail) segments are out of scope for this verifier —
    the line-by-line AuditChain.verify() covers them."""
    idx = SegmentIndex(schema_version=1, segments=(
        SegmentMeta(
            seq_start=0, seq_end=None, file="audit_chain_2026-05.jsonl",
            month="2026-05", sealed=False, merkle_root=None,
        ),
    ))
    r = verify_sealed_segments(index=idx, segment_dir=tmp_path)
    assert r.ok is True
    assert r.segments_verified == 0
    assert r.issues == ()


def test_verify_sealed_segments_reports_all_issues_in_one_pass(tmp_path):
    """Multi-segment index with a mix of clean + tampered segments
    reports both — caller sees the full picture in one call rather
    than bailing on first issue."""
    f1 = "audit_chain_2026-01.jsonl"
    f2 = "audit_chain_2026-02.jsonl"
    # Clean segment.
    _write_segment(tmp_path / f1, [
        {"seq": 0, "entry_hash": "a" * 64},
        {"seq": 1, "entry_hash": "b" * 64},
    ])
    # Tampered segment.
    _write_segment(tmp_path / f2, [
        {"seq": 2, "entry_hash": "c" * 64},
        {"seq": 3, "entry_hash": "d" * 64},
    ])

    idx = SegmentIndex(schema_version=1, segments=(
        SegmentMeta(
            seq_start=0, seq_end=1, file=f1, month="2026-01",
            sealed=True, merkle_root=merkle_root(["a" * 64, "b" * 64]),
        ),
        SegmentMeta(
            seq_start=2, seq_end=3, file=f2, month="2026-02",
            sealed=True, merkle_root=merkle_root(["x" * 64, "y" * 64]),  # wrong
        ),
    ))

    r = verify_sealed_segments(index=idx, segment_dir=tmp_path)
    assert r.ok is False
    assert r.segments_verified == 1  # the clean one
    assert len(r.issues) == 1  # the tampered one
    assert r.issues[0].kind == "merkle_mismatch"
    assert r.issues[0].segment_file == f2


def test_verify_sealed_segments_issue_shape():
    """SegmentVerifyIssue is frozen + has the locked field set."""
    issue = SegmentVerifyIssue(
        kind="merkle_mismatch",
        segment_file="x.jsonl",
        details="test",
    )
    with pytest.raises(Exception):
        issue.kind = "other"  # noqa
