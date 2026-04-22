"""Unit tests for the audit chain.

Design reference: docs/decisions/ADR-0005-audit-chain.md
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import (
    AUDIT_SCHEMA_VERSION,
    AuditChain,
    AuditChainError,
    ChainEntry,
    GENESIS_EVENT_TYPE,
    GENESIS_PREV_HASH,
    InvalidAppendError,
    KNOWN_EVENT_TYPES,
    VerificationResult,
    _canonical_hash_input,
    _sha256_hex,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_lines(path: Path) -> list[dict]:
    out = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.rstrip("\n")
            if raw:
                out.append(json.loads(raw))
    return out


# ---------------------------------------------------------------------------
# Genesis / construction
# ---------------------------------------------------------------------------
class TestGenesis:
    def test_auto_creates_file_and_genesis(self, tmp_path: Path) -> None:
        chain_path = tmp_path / "audit" / "chain.jsonl"
        assert not chain_path.exists()
        chain = AuditChain(chain_path)
        assert chain_path.exists()
        assert chain.head is not None
        assert chain.head.seq == 0
        assert chain.head.prev_hash == GENESIS_PREV_HASH
        assert chain.head.event_type == GENESIS_EVENT_TYPE
        assert chain.head.agent_dna is None
        assert chain.head.event_data == {"schema_version": AUDIT_SCHEMA_VERSION}

    def test_genesis_hash_is_sha256_hex(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        assert isinstance(chain.head, ChainEntry)
        assert len(chain.head.entry_hash) == 64
        assert all(c in "0123456789abcdef" for c in chain.head.entry_hash)

    def test_reopen_preserves_head(self, tmp_path: Path) -> None:
        path = tmp_path / "chain.jsonl"
        chain1 = AuditChain(path)
        entry = chain1.append("agent_created", {"agent_name": "A"})
        # Reopen — should not rewrite genesis; head should be the appended entry.
        chain2 = AuditChain(path)
        assert chain2.head is not None
        assert chain2.head.seq == 1
        assert chain2.head.entry_hash == entry.entry_hash

    def test_empty_existing_file_triggers_genesis(self, tmp_path: Path) -> None:
        path = tmp_path / "chain.jsonl"
        path.touch()
        assert path.exists() and path.stat().st_size == 0
        chain = AuditChain(path)
        assert chain.head is not None
        assert chain.head.seq == 0


# ---------------------------------------------------------------------------
# append()
# ---------------------------------------------------------------------------
class TestAppend:
    def test_returns_committed_entry_with_expected_fields(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        entry = chain.append(
            "agent_created",
            {"agent_name": "NW-01", "role": "network_watcher"},
            agent_dna="abcdef123456",
        )
        assert entry.seq == 1
        assert entry.prev_hash == chain.head.prev_hash  # head now IS this entry
        assert entry.agent_dna == "abcdef123456"
        assert entry.event_type == "agent_created"
        assert entry.event_data == {"agent_name": "NW-01", "role": "network_watcher"}
        assert len(entry.entry_hash) == 64

    def test_head_advances(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        old_head = chain.head
        new_entry = chain.append("agent_created", {"agent_name": "A"})
        assert chain.head is new_entry
        assert new_entry.prev_hash == old_head.entry_hash

    def test_sequential_appends_link_hashes(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        e1 = chain.append("agent_created", {"agent_name": "A"})
        e2 = chain.append("agent_created", {"agent_name": "B"})
        e3 = chain.append("finding_emitted", {"severity": "low"})
        assert e1.seq == 1 and e2.seq == 2 and e3.seq == 3
        assert e2.prev_hash == e1.entry_hash
        assert e3.prev_hash == e2.entry_hash

    def test_empty_event_data_defaults_to_empty_dict(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        entry = chain.append("agent_created")
        assert entry.event_data == {}

    def test_event_data_is_defensively_copied(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        payload = {"agent_name": "A"}
        entry = chain.append("agent_created", payload)
        payload["agent_name"] = "MUTATED"
        assert entry.event_data == {"agent_name": "A"}

    def test_rejects_empty_event_type(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        with pytest.raises(InvalidAppendError):
            chain.append("", {})

    def test_rejects_non_string_event_type(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        with pytest.raises(InvalidAppendError):
            chain.append(None, {})  # type: ignore[arg-type]

    def test_writes_one_line_per_append(self, tmp_path: Path) -> None:
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        chain.append("agent_created", {"agent_name": "A"})
        chain.append("agent_created", {"agent_name": "B"})
        lines = _read_lines(path)
        assert len(lines) == 3  # genesis + 2 appends
        assert [l["seq"] for l in lines] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Hash properties
# ---------------------------------------------------------------------------
class TestHash:
    def test_canonical_hash_input_deterministic(self) -> None:
        a = _canonical_hash_input(
            seq=1, prev_hash="x", agent_dna="dna",
            event_type="agent_created",
            event_data={"b": 2, "a": 1},  # unsorted
        )
        b = _canonical_hash_input(
            seq=1, prev_hash="x", agent_dna="dna",
            event_type="agent_created",
            event_data={"a": 1, "b": 2},  # reordered
        )
        assert a == b

    def test_hash_excludes_timestamp(self, tmp_path: Path) -> None:
        """Two entries with identical canonical fields produce identical hashes
        regardless of when they were written. We prove this by hashing the same
        canonical input twice — timestamp is not part of the input."""
        payload = dict(seq=1, prev_hash="abc", agent_dna=None,
                       event_type="agent_created", event_data={"x": 1})
        h1 = _sha256_hex(_canonical_hash_input(**payload))
        h2 = _sha256_hex(_canonical_hash_input(**payload))
        assert h1 == h2

    def test_agent_dna_changes_hash(self) -> None:
        base = dict(seq=1, prev_hash="abc",
                    event_type="agent_created", event_data={"x": 1})
        h1 = _sha256_hex(_canonical_hash_input(agent_dna="A", **base))
        h2 = _sha256_hex(_canonical_hash_input(agent_dna="B", **base))
        assert h1 != h2

    def test_event_type_changes_hash(self) -> None:
        base = dict(seq=1, prev_hash="abc", agent_dna=None, event_data={})
        h1 = _sha256_hex(_canonical_hash_input(event_type="agent_created", **base))
        h2 = _sha256_hex(_canonical_hash_input(event_type="agent_spawned", **base))
        assert h1 != h2


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------
class TestVerify:
    def test_fresh_chain_verifies(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        result = chain.verify()
        assert isinstance(result, VerificationResult)
        assert result.ok is True
        assert result.entries_verified == 1  # genesis
        assert result.broken_at_seq is None
        assert result.reason is None
        assert result.unknown_event_types == ()

    def test_appended_chain_verifies(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        for i in range(5):
            chain.append("agent_created", {"n": i})
        result = chain.verify()
        assert result.ok is True
        assert result.entries_verified == 6  # genesis + 5

    def test_unknown_event_type_is_warning_not_failure(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        chain.append("agent_created", {"x": 1})
        chain.append("forward_compat_event_xyz", {"y": 2})
        result = chain.verify()
        assert result.ok is True
        assert "forward_compat_event_xyz" in result.unknown_event_types
        # All KNOWN types should NOT appear in the warnings list.
        for known in KNOWN_EVENT_TYPES:
            assert known not in result.unknown_event_types

    def test_tampered_event_data_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        chain.append("agent_created", {"agent_name": "A"})
        chain.append("agent_created", {"agent_name": "B"})

        # Rewrite the middle entry's event_data but keep its hash — breaks entry_hash check.
        lines = _read_lines(path)
        lines[1]["event_data"]["agent_name"] = "HACKED"
        with path.open("w", encoding="utf-8") as f:
            for obj in lines:
                f.write(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")

        # Reopen — we bypass the constructor's head-recompute by building fresh.
        result = AuditChain(path).verify()
        assert result.ok is False
        assert result.broken_at_seq == 1
        assert result.reason == "entry_hash mismatch"

    def test_tampered_prev_hash_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        chain.append("agent_created", {"x": 1})
        chain.append("agent_created", {"x": 2})

        lines = _read_lines(path)
        lines[2]["prev_hash"] = "0" * 64
        with path.open("w", encoding="utf-8") as f:
            for obj in lines:
                f.write(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")

        result = AuditChain(path).verify()
        assert result.ok is False
        assert result.broken_at_seq == 2
        assert result.reason == "prev_hash mismatch"

    def test_seq_gap_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        chain.append("agent_created", {"x": 1})

        # Manually forge a seq=5 entry to skip over seq=2.
        lines = _read_lines(path)
        forged = dict(lines[-1])
        forged["seq"] = 5
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")

        result = AuditChain(path).verify()
        assert result.ok is False
        assert result.broken_at_seq == 5
        assert "seq gap" in result.reason

    def test_invalid_json_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        chain.append("agent_created", {"x": 1})
        with path.open("a", encoding="utf-8") as f:
            f.write("{not valid json\n")

        result = AuditChain(path).verify()
        assert result.ok is False
        assert result.reason is not None
        assert "invalid JSON" in result.reason

    def test_entry_missing_required_field_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        chain.append("agent_created", {"x": 1})
        # Append a line missing "entry_hash".
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"seq": 2, "timestamp": "t", "prev_hash": "x",
                                "event_type": "agent_created"}) + "\n")
        result = AuditChain(path).verify()
        assert result.ok is False
        assert "missing required field" in result.reason


# ---------------------------------------------------------------------------
# read_all / ChainEntry serialization
# ---------------------------------------------------------------------------
class TestReadAll:
    def test_returns_all_entries_in_order(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        chain.append("agent_created", {"n": 1})
        chain.append("agent_created", {"n": 2})
        entries = chain.read_all()
        assert [e.seq for e in entries] == [0, 1, 2]
        assert entries[0].event_type == GENESIS_EVENT_TYPE
        assert entries[1].event_data == {"n": 1}
        assert entries[2].event_data == {"n": 2}

    def test_raises_on_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        with path.open("a", encoding="utf-8") as f:
            f.write("{garbage\n")
        with pytest.raises(AuditChainError):
            chain.read_all()

    def test_to_json_line_is_canonical(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        entry = chain.append("agent_created", {"b": 2, "a": 1})
        line = entry.to_json_line()
        assert line.endswith("\n")
        # Canonical: keys sorted, no whitespace
        assert ", " not in line
        assert ": " not in line
        parsed = json.loads(line)
        assert parsed["seq"] == entry.seq
        assert parsed["event_data"] == {"a": 1, "b": 2}
