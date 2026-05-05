"""Conformance §2 — Audit chain schema.

Spec: docs/spec/kernel-api-v0.6.md §2.
"""
from __future__ import annotations

import hashlib
import json

import httpx


# ----- §2.1 — JSONL line shape -------------------------------------------


REQUIRED_TOP_LEVEL_FIELDS = {
    "seq", "timestamp", "agent_dna", "event_type", "event_data",
    "prev_hash", "entry_hash",
}


def test_section2_jsonl_line_shape(client: httpx.Client) -> None:
    """§2.1: every chain entry has the seven top-level fields."""
    resp = client.get("/audit/tail", params={"n": 50})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "events" in body, f"response missing 'events': {body}"
    for entry in body["events"]:
        missing = REQUIRED_TOP_LEVEL_FIELDS - set(entry.keys())
        assert not missing, (
            f"audit entry missing required fields {missing}; "
            f"spec §2.1 requires all seven. Entry: {entry}"
        )


def test_section2_seq_monotonic(client: httpx.Client) -> None:
    """§2.1: seq is monotonically increasing from 1.

    We can only check the last N — the canonical chain may have
    been rotated or trimmed at the head — but among the returned
    entries seq must strictly increase.
    """
    body = client.get("/audit/tail", params={"n": 50}).json()
    seqs = [entry["seq"] for entry in body["events"]]
    if len(seqs) >= 2:
        for i in range(1, len(seqs)):
            assert seqs[i] > seqs[i - 1], (
                f"audit chain seq not monotonic: index {i-1}→{i} went "
                f"{seqs[i-1]}→{seqs[i]}. Spec §2.1 requires strictly increasing."
            )


# ----- §2.2 — hash discipline --------------------------------------------


def _canonical_event(entry: dict) -> str:
    """Compute the canonical-JSON form per spec §2.2 (excluding entry_hash)."""
    body = {
        "seq": entry["seq"],
        "timestamp": entry["timestamp"],
        "agent_dna": entry["agent_dna"],
        "event_type": entry["event_type"],
        "event_data": entry["event_data"],
        "prev_hash": entry["prev_hash"],
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def test_section2_hash_chain_integrity(client: httpx.Client) -> None:
    """§2.2: entry_hash = sha256(canonical_event); prev_hash chains correctly.

    Per spec, every entry's entry_hash is sha256 of the canonical-JSON
    serialization of the entry (excluding entry_hash itself). And each
    entry's prev_hash equals the previous entry's entry_hash.
    """
    body = client.get("/audit/tail", params={"n": 20}).json()
    events = body["events"]
    if len(events) < 2:
        # Empty / nearly-empty chain — nothing to verify but not a fail.
        return

    for i, entry in enumerate(events):
        canonical = _canonical_event(entry)
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert entry["entry_hash"] == expected_hash, (
            f"entry_hash mismatch at seq={entry['seq']}: "
            f"computed {expected_hash} but entry says {entry['entry_hash']}. "
            f"Spec §2.2 requires entry_hash = sha256(canonical_event)."
        )
        if i > 0:
            prev = events[i - 1]
            assert entry["prev_hash"] == prev["entry_hash"], (
                f"prev_hash chain broken between seq={prev['seq']} → "
                f"seq={entry['seq']}. Spec §2.2 requires linkage."
            )


# ----- §2.4 — event type catalog -----------------------------------------


# Spec §2.4 — spot-check that core event-type families are documented as
# emittable. We don't enforce a specific set (the catalog is allowed to
# grow per §2.4) but assert the response is a list of strings if the
# kernel exposes it.

def test_section2_event_types_have_strings(client: httpx.Client) -> None:
    """§2.4: every entry's event_type is a non-empty string.

    The spec doesn't pin which 70 event types must appear in the chain
    (depends on what's been dispatched). It DOES pin that event_type is
    a string. This test enforces that minimal contract.
    """
    body = client.get("/audit/tail", params={"n": 50}).json()
    for entry in body["events"]:
        assert isinstance(entry["event_type"], str), (
            f"event_type must be a string per spec §2.1: {entry}"
        )
        assert entry["event_type"], "event_type must be non-empty"
