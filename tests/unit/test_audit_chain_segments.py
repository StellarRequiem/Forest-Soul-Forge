"""ADR-0073 T1 (B291) — audit chain segmentation tests.

Covers:
  - SegmentIndex load/save round-trip
  - missing index file returns empty index (benign)
  - malformed JSON raises hard
  - schema mismatch raises hard
  - missing required segment fields raises hard
  - SegmentIndex.current() returns the unsealed segment
  - SegmentIndex.for_seq() finds the right segment
  - SegmentIndex.sealed_segments() filters correctly
  - merkle_root: empty input, single hash, two hashes, odd count
    (last duplicated)
  - segment filename convention
  - current_segment_month returns YYYY-MM
  - append_segment_entry writes + line-terminates
  - audit_chain_anchor event type registered
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.core.audit_chain_segments import (
    SCHEMA_VERSION,
    SegmentIndex,
    SegmentIndexError,
    SegmentMeta,
    append_segment_entry,
    current_segment_month,
    load_segment_index,
    merkle_root,
    save_segment_index,
    segment_filename_for_month,
)


# ---------------------------------------------------------------------------
# load/save round-trip
# ---------------------------------------------------------------------------
def test_load_missing_index_returns_empty(tmp_path):
    idx = load_segment_index(tmp_path / "nope.json")
    assert idx.segments == ()
    assert idx.schema_version == SCHEMA_VERSION


def test_save_then_load_round_trips(tmp_path):
    idx = SegmentIndex(
        schema_version=1,
        segments=(
            SegmentMeta(
                seq_start=1, seq_end=100, file="a.jsonl",
                month="2026-05", sealed=True,
                merkle_root="abc123",
            ),
            SegmentMeta(
                seq_start=101, seq_end=None, file="b.jsonl",
                month="2026-06", sealed=False,
            ),
        ),
    )
    p = tmp_path / "index.json"
    save_segment_index(idx, p)
    loaded = load_segment_index(p)
    assert len(loaded.segments) == 2
    assert loaded.segments[0].merkle_root == "abc123"
    assert loaded.segments[1].seq_end is None


# ---------------------------------------------------------------------------
# Loader failures
# ---------------------------------------------------------------------------
def test_load_malformed_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json")
    with pytest.raises(SegmentIndexError, match="malformed JSON"):
        load_segment_index(p)


def test_load_schema_mismatch_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"schema_version": 99, "segments": []}))
    with pytest.raises(SegmentIndexError, match="schema_version"):
        load_segment_index(p)


def test_load_top_level_not_object_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[]")
    with pytest.raises(SegmentIndexError, match="JSON object"):
        load_segment_index(p)


def test_load_missing_required_field_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "segments": [
            {"seq_start": 1, "file": "a.jsonl"},  # missing month + sealed
        ],
    }))
    with pytest.raises(SegmentIndexError, match="missing required"):
        load_segment_index(p)


# ---------------------------------------------------------------------------
# SegmentIndex methods
# ---------------------------------------------------------------------------
def _idx_two_segments() -> SegmentIndex:
    return SegmentIndex(
        schema_version=1,
        segments=(
            SegmentMeta(1, 100, "a.jsonl", "2026-05", True, "root1"),
            SegmentMeta(101, None, "b.jsonl", "2026-06", False),
        ),
    )


def test_current_returns_unsealed():
    assert _idx_two_segments().current().file == "b.jsonl"


def test_current_returns_none_when_empty():
    idx = SegmentIndex(schema_version=1, segments=())
    assert idx.current() is None


def test_for_seq_finds_sealed():
    idx = _idx_two_segments()
    assert idx.for_seq(50).file == "a.jsonl"
    assert idx.for_seq(100).file == "a.jsonl"


def test_for_seq_finds_tail():
    idx = _idx_two_segments()
    assert idx.for_seq(101).file == "b.jsonl"
    assert idx.for_seq(99999).file == "b.jsonl"  # tail's seq_end is None


def test_for_seq_misses_when_before_first():
    idx = _idx_two_segments()
    assert idx.for_seq(0) is None


def test_sealed_segments_filters():
    idx = _idx_two_segments()
    sealed = idx.sealed_segments()
    assert len(sealed) == 1
    assert sealed[0].file == "a.jsonl"


# ---------------------------------------------------------------------------
# Merkle root
# ---------------------------------------------------------------------------
def test_merkle_root_empty():
    """Empty input convention: sha256 of empty bytes."""
    expected = hashlib.sha256(b"").hexdigest()
    assert merkle_root([]) == expected


def test_merkle_root_single_hash():
    """Single-hash Merkle is the hash itself."""
    h = "a" * 64
    assert merkle_root([h]) == h


def test_merkle_root_two_hashes():
    """Two hashes: sha256(concat(hex_decode(h1), hex_decode(h2)))."""
    h1 = "a" * 64
    h2 = "b" * 64
    expected = hashlib.sha256(
        bytes.fromhex(h1) + bytes.fromhex(h2)
    ).hexdigest()
    assert merkle_root([h1, h2]) == expected


def test_merkle_root_odd_count_duplicates_last():
    """Three hashes: last duplicated to pair. Verify by manual
    computation."""
    h1 = "11" * 32
    h2 = "22" * 32
    h3 = "33" * 32
    # Level 1: pair (h1, h2) → p1; pair (h3, h3) → p2
    p1 = hashlib.sha256(bytes.fromhex(h1) + bytes.fromhex(h2)).digest()
    p2 = hashlib.sha256(bytes.fromhex(h3) + bytes.fromhex(h3)).digest()
    # Level 2: pair (p1, p2) → root
    expected = hashlib.sha256(p1 + p2).hexdigest()
    assert merkle_root([h1, h2, h3]) == expected


def test_merkle_root_deterministic():
    """Same input → same output."""
    hashes = ["a" * 64, "b" * 64, "c" * 64, "d" * 64]
    assert merkle_root(hashes) == merkle_root(hashes)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def test_segment_filename_convention():
    assert segment_filename_for_month("2026-05") == "audit_chain_2026-05.jsonl"


def test_current_segment_month_format():
    """Returns YYYY-MM matching UTC now."""
    out = current_segment_month()
    # Format check: 4 digits + dash + 2 digits.
    parts = out.split("-")
    assert len(parts) == 2
    assert len(parts[0]) == 4
    assert len(parts[1]) == 2
    assert parts[0].isdigit() and parts[1].isdigit()


def test_append_segment_entry_writes_with_newline(tmp_path):
    p = tmp_path / "seg.jsonl"
    append_segment_entry(p, '{"seq":1}')
    assert p.read_text() == '{"seq":1}\n'


def test_append_segment_entry_appends_without_double_newline(tmp_path):
    p = tmp_path / "seg.jsonl"
    append_segment_entry(p, '{"seq":1}\n')  # already terminated
    append_segment_entry(p, '{"seq":2}')
    text = p.read_text()
    assert text == '{"seq":1}\n{"seq":2}\n'


# ---------------------------------------------------------------------------
# Audit event type
# ---------------------------------------------------------------------------
def test_audit_chain_anchor_event_registered():
    assert "audit_chain_anchor" in KNOWN_EVENT_TYPES
