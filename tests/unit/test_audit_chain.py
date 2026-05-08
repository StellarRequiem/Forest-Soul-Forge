"""Unit tests for the audit chain.

Design reference: docs/decisions/ADR-0005-audit-chain.md
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import (
    AUDIT_SCHEMA_VERSION,
    AuditChain,
    AuditChainError,
    ChainEntry,
    ForkScanResult,
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


# ---------------------------------------------------------------------------
# tail — reads the canonical JSONL, primary path for /audit/tail
# ---------------------------------------------------------------------------
class TestTail:
    """``tail(n)`` is what /audit/tail uses; the registry mirror only sees
    lifespan-time events, so anything appended at runtime (tool dispatches,
    agent_delegated, skill_invoked) MUST come from the canonical JSONL or
    the operator can't see live activity. These tests pin that contract.
    """

    def test_returns_most_recent_n_newest_first(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        # Genesis is seq=0; append five events → seqs 1..5.
        for i in range(1, 6):
            chain.append("agent_created", {"n": i})
        tail = chain.tail(3)
        assert [e.seq for e in tail] == [5, 4, 3]
        assert tail[0].event_data == {"n": 5}
        assert tail[2].event_data == {"n": 3}

    def test_returns_runtime_events_not_just_lifespan(self, tmp_path: Path) -> None:
        # Models the bug we're fixing: registry only sees lifespan events;
        # the chain sees everything appended after construction. tail()
        # must reflect appends made *after* the chain object exists.
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        # "Lifespan-time" event.
        chain.append("agent_created", {"phase": "boot"})
        # "Runtime" event — what the registry mirror would miss.
        chain.append("tool_call_dispatched", {"phase": "runtime"})
        tail = chain.tail(2)
        types = [e.event_type for e in tail]
        assert "tool_call_dispatched" in types
        assert types[0] == "tool_call_dispatched"  # newest first

    def test_n_zero_returns_empty(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        chain.append("agent_created", {})
        assert chain.tail(0) == []

    def test_n_larger_than_chain_returns_all(self, tmp_path: Path) -> None:
        chain = AuditChain(tmp_path / "chain.jsonl")
        chain.append("agent_created", {"n": 1})
        chain.append("agent_created", {"n": 2})
        # Chain has 3 entries (genesis + 2 appends); ask for 100.
        tail = chain.tail(100)
        assert len(tail) == 3
        # Newest first.
        assert [e.seq for e in tail] == [2, 1, 0]

    def test_tolerates_malformed_lines(self, tmp_path: Path) -> None:
        # Mirrors _recompute_head's tolerance: tail() should keep working
        # even when verify() would flag a structural break, so the
        # operator can still see recent events while diagnosing the break.
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        chain.append("agent_created", {"n": 1})
        with path.open("a", encoding="utf-8") as f:
            f.write("{not json\n")
        chain.append("agent_created", {"n": 2})
        tail = chain.tail(10)
        # Genesis + two valid appends; the garbage line is silently skipped.
        assert len(tail) == 3
        assert tail[0].event_data == {"n": 2}


class TestConcurrentAppend:
    """B199 regression coverage. Without the internal RLock added to
    ``AuditChain.append`` in B199, two threads racing the same chain
    instance could both read ``self._head`` before either advanced it,
    both compute ``next_seq = prev.seq + 1`` against the same prev_hash,
    both write a line, and both set ``self._head`` — leaving the chain
    on disk with two entries sharing the same seq + prev_hash. That is
    exactly the fork signature that surfaced at seqs 3728/3735-3738/3740
    in the live ``examples/audit_chain.jsonl`` (forensic record:
    ``docs/audits/2026-05-08-chain-fork-incident.md``).

    These tests fire many concurrent ``append`` calls and assert
    (a) every seq in the chain is unique, (b) ``verify()`` returns
    ``ok=True``, (c) the head's seq matches the total append count.
    Pre-B199 these would flake — sometimes by a lot, sometimes not at
    all, depending on GIL release timing — which is the *worst* kind
    of regression to catch later. A passing run here is necessary but
    not sufficient (concurrency tests rarely prove absence of races);
    a *failing* run is conclusive evidence that the lock is missing
    or wrong.
    """

    def test_no_duplicate_seqs_under_thread_storm(self, tmp_path: Path) -> None:
        """16 threads × 50 appends each = 800 total.

        Worker function reads no shared state besides the chain itself
        — every thread writes the same event type with a per-iteration
        payload tag, so collisions in the on-disk artifact are
        attributable to the lock rather than to event_data races.
        """
        chain = AuditChain(tmp_path / "chain.jsonl")
        n_threads = 16
        per_thread = 50

        def worker(worker_id: int) -> list[int]:
            seqs: list[int] = []
            for i in range(per_thread):
                entry = chain.append(
                    "agent_created",
                    {"worker": worker_id, "i": i},
                )
                seqs.append(entry.seq)
            return seqs

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            results = list(pool.map(worker, range(n_threads)))

        # Every returned seq across every thread must be unique.
        all_seqs: list[int] = [s for run in results for s in run]
        assert len(all_seqs) == n_threads * per_thread
        assert len(set(all_seqs)) == len(all_seqs), (
            "duplicate seqs returned from append() — internal lock missing or broken"
        )

        # Chain on disk must verify clean: 800 appends + 1 genesis = 801 entries,
        # head.seq must be exactly n_threads * per_thread.
        result = chain.verify()
        assert result.ok, f"verify() failed: {result.reason} at seq {result.broken_at_seq}"
        assert result.entries_verified == n_threads * per_thread + 1
        assert chain.head is not None
        assert chain.head.seq == n_threads * per_thread

    def test_disk_artifact_seqs_strictly_increasing(self, tmp_path: Path) -> None:
        """Beyond the API-level uniqueness check, the on-disk JSONL
        must show seqs 0..N in strictly increasing order. A pre-B199
        race could yield two entries with the same seq AND prev_hash
        on disk — verify() catches the seq gap that this creates, but
        we want a separate explicit assertion against the file because
        a future refactor could break verify() while leaving disk
        corruption invisible.
        """
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)

        n_threads = 8
        per_thread = 25

        def worker(worker_id: int) -> None:
            for i in range(per_thread):
                chain.append("agent_created", {"w": worker_id, "i": i})

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(worker, range(n_threads)))

        seqs_on_disk: list[int] = []
        with path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                seqs_on_disk.append(json.loads(raw)["seq"])

        # Genesis + n_threads*per_thread appends.
        expected_count = n_threads * per_thread + 1
        assert len(seqs_on_disk) == expected_count

        # Strictly increasing 0..N — no duplicates, no gaps, no out-of-order.
        assert seqs_on_disk == list(range(expected_count))

    # ----- B199 Layer 3b: scan_for_forks ---------------------------------

    def test_scan_for_forks_clean_chain(self, tmp_path: Path) -> None:
        """A chain with no anomalies returns ok=True and empty lists."""
        chain = AuditChain(tmp_path / "chain.jsonl")
        for i in range(20):
            chain.append("agent_created", {"i": i})

        result = chain.scan_for_forks()
        assert isinstance(result, ForkScanResult)
        assert result.ok
        assert result.entries_scanned == 21  # 20 + genesis
        assert result.duplicate_seqs == ()
        assert result.hash_mismatches == ()

    def test_scan_for_forks_detects_duplicate_seq(self, tmp_path: Path) -> None:
        """Hand-crafted chain file with two entries sharing seq=2 — must
        surface in duplicate_seqs even though both entries individually
        have valid (self-consistent) hashes. This is the canonical race
        signature from the 2026-05-08 incident.
        """
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        chain.append("agent_created", {"i": 1})
        chain.append("agent_created", {"i": 2})
        # Build a duplicate seq=2 entry by hand. Compute its hash so it's
        # internally consistent — the only anomaly is the duplicated seq.
        head_after = chain.head
        assert head_after is not None and head_after.seq == 2
        # Construct an entry with seq=2 and the SAME prev_hash the original
        # seq=2 entry used (which is the seq=1 hash).
        # Easiest: use the same prev_hash as the existing head's prev_hash.
        # That mimics two writers racing the same head pointer.
        entries = chain.read_all()
        prev_for_seq_two = entries[1].entry_hash  # seq=1's hash
        forged_data = {"i": "forged"}
        forged_hash = _sha256_hex(_canonical_hash_input(
            seq=2,
            prev_hash=prev_for_seq_two,
            agent_dna=None,
            event_type="agent_created",
            event_data=forged_data,
        ))
        forged_entry = {
            "seq": 2,
            "timestamp": "2026-05-08T00:00:00Z",
            "prev_hash": prev_for_seq_two,
            "entry_hash": forged_hash,
            "agent_dna": None,
            "event_type": "agent_created",
            "event_data": forged_data,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(forged_entry, sort_keys=True, separators=(",", ":")) + "\n")

        # Re-open so the in-memory head doesn't skew the scan (scan reads
        # from disk regardless, but this matches operator workflow).
        chain2 = AuditChain(path)
        result = chain2.scan_for_forks()
        assert not result.ok
        assert 2 in result.duplicate_seqs
        # The forged entry's own hash is correct; only the seq is duplicated.
        assert result.hash_mismatches == ()

    def test_scan_for_forks_detects_hash_mismatch(self, tmp_path: Path) -> None:
        """A hand-edited entry whose entry_hash no longer matches its
        canonical-form payload must surface in hash_mismatches. Distinct
        from duplicate_seqs — this is tampering or canonical-form drift,
        not a race.
        """
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        chain.append("agent_created", {"i": 1})
        chain.append("agent_created", {"i": 2})

        # Read the file, mutate event_data on seq=1 without recomputing
        # entry_hash.
        lines = []
        with path.open("r", encoding="utf-8") as f:
            for raw in f:
                lines.append(json.loads(raw.rstrip("\n")))
        lines[1]["event_data"] = {"i": "tampered"}
        with path.open("w", encoding="utf-8") as f:
            for obj in lines:
                f.write(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")

        chain2 = AuditChain(path)
        result = chain2.scan_for_forks()
        assert not result.ok
        assert 1 in result.hash_mismatches
        assert result.duplicate_seqs == ()

    def test_scan_for_forks_does_not_short_circuit(self, tmp_path: Path) -> None:
        """The whole point of scan_for_forks vs verify: it walks the
        ENTIRE chain and reports every anomaly. Build a chain with TWO
        forks at different seqs and confirm both are reported.
        """
        path = tmp_path / "chain.jsonl"
        chain = AuditChain(path)
        for i in range(5):
            chain.append("agent_created", {"i": i})

        entries = chain.read_all()
        # Forge a duplicate at seq=2 AND seq=4.
        for target_seq in (2, 4):
            prev = entries[target_seq - 1].entry_hash
            forged_data = {"forged_at": target_seq}
            forged_hash = _sha256_hex(_canonical_hash_input(
                seq=target_seq, prev_hash=prev, agent_dna=None,
                event_type="agent_created", event_data=forged_data,
            ))
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "seq": target_seq,
                    "timestamp": "2026-05-08T00:00:00Z",
                    "prev_hash": prev,
                    "entry_hash": forged_hash,
                    "agent_dna": None,
                    "event_type": "agent_created",
                    "event_data": forged_data,
                }, sort_keys=True, separators=(",", ":")) + "\n")

        chain2 = AuditChain(path)
        result = chain2.scan_for_forks()
        assert not result.ok
        # BOTH forks must be reported, not just the first.
        assert 2 in result.duplicate_seqs
        assert 4 in result.duplicate_seqs

    def test_lock_is_reentrant(self, tmp_path: Path) -> None:
        """``self._append_lock`` is an ``RLock`` (not a ``Lock``) so a
        thread that already holds it can re-acquire without deadlock.
        Important because future code paths may compose chain.append
        inside other lock-holding work (e.g. a registry transaction
        that also writes a chain entry, all under app.state.write_lock,
        which is itself an RLock for the same reason). If somebody
        downgrades to a plain Lock the test deadlocks; pytest-timeout
        isn't required because we acquire-and-release on the same
        thread synchronously.
        """
        chain = AuditChain(tmp_path / "chain.jsonl")
        # Acquire the lock from the current thread, then call append —
        # which itself tries to acquire the same lock. If it's an RLock
        # this re-acquires fine; if someone changed it to Lock this
        # blocks forever and the test hangs.
        with chain._append_lock:
            entry = chain.append("agent_created", {"reentrant": True})
        assert entry.seq == 1  # genesis is seq=0
