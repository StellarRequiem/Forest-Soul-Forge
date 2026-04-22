"""Sandbox verification for the audit chain.

Run: python3 scripts/verify_audit_chain.py

Uses a temp directory so no state leaks into the repo.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from forest_soul_forge.core.audit_chain import (  # noqa: E402
    AUDIT_SCHEMA_VERSION,
    AuditChain,
    GENESIS_EVENT_TYPE,
    GENESIS_PREV_HASH,
    InvalidAppendError,
    KNOWN_EVENT_TYPES,
    _canonical_hash_input,
    _sha256_hex,
)


def _rewrite(path: Path, lines: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for obj in lines:
            f.write(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")


def _read(path: Path) -> list[dict]:
    out = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.rstrip("\n")
            if raw:
                out.append(json.loads(raw))
    return out


def main() -> int:
    checks: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # --- genesis auto-creates ----------------------------------------
        p1 = root / "one" / "chain.jsonl"
        c1 = AuditChain(p1)
        checks.append(("genesis: file auto-created", p1.exists()))
        checks.append(("genesis: head is seq=0", c1.head is not None and c1.head.seq == 0))
        checks.append(("genesis: prev_hash is GENESIS", c1.head.prev_hash == GENESIS_PREV_HASH))
        checks.append(("genesis: event_type is chain_created", c1.head.event_type == GENESIS_EVENT_TYPE))
        checks.append(("genesis: schema_version embedded", c1.head.event_data.get("schema_version") == AUDIT_SCHEMA_VERSION))
        checks.append(("genesis: hash is 64 hex", len(c1.head.entry_hash) == 64))

        # --- reopen preserves state --------------------------------------
        e1 = c1.append("agent_created", {"agent_name": "A"})
        c1_reopen = AuditChain(p1)
        checks.append(("reopen: head preserved", c1_reopen.head is not None and c1_reopen.head.entry_hash == e1.entry_hash))
        checks.append(("reopen: does not rewrite genesis", len(_read(p1)) == 2))

        # --- linkage -----------------------------------------------------
        e2 = c1.append("agent_created", {"agent_name": "B"})
        e3 = c1.append("finding_emitted", {"severity": "low"})
        checks.append(("linkage: seqs monotonic", [e1.seq, e2.seq, e3.seq] == [1, 2, 3]))
        checks.append(("linkage: e2.prev == e1.hash", e2.prev_hash == e1.entry_hash))
        checks.append(("linkage: e3.prev == e2.hash", e3.prev_hash == e2.entry_hash))

        # --- defensive copy on event_data --------------------------------
        payload = {"agent_name": "X"}
        entry = c1.append("agent_created", payload)
        payload["agent_name"] = "MUTATED"
        checks.append(("defensive copy: mutation doesn't leak", entry.event_data == {"agent_name": "X"}))

        # --- rejects malformed appends -----------------------------------
        try:
            c1.append("", {})
            checks.append(("rejects empty event_type", False))
        except InvalidAppendError:
            checks.append(("rejects empty event_type", True))

        # --- verify: clean chain -----------------------------------------
        r = c1.verify()
        checks.append(("verify: clean chain ok", r.ok and r.reason is None))
        checks.append(("verify: entry count matches appends", r.entries_verified == 5))

        # --- canonical hash input determinism ----------------------------
        h_a = _sha256_hex(_canonical_hash_input(seq=1, prev_hash="x", agent_dna="d",
                                                event_type="agent_created",
                                                event_data={"b": 2, "a": 1}))
        h_b = _sha256_hex(_canonical_hash_input(seq=1, prev_hash="x", agent_dna="d",
                                                event_type="agent_created",
                                                event_data={"a": 1, "b": 2}))
        checks.append(("canonical hash: key order irrelevant", h_a == h_b))

        # --- unknown event type = warning, not failure -------------------
        p2 = root / "two" / "chain.jsonl"
        c2 = AuditChain(p2)
        c2.append("agent_created", {"x": 1})
        c2.append("future_event_qqq", {"y": 2})
        r2 = c2.verify()
        checks.append(("unknown event: verify still ok", r2.ok))
        checks.append(("unknown event: reported in warnings", "future_event_qqq" in r2.unknown_event_types))
        checks.append(("unknown event: known types absent from warnings",
                       all(k not in r2.unknown_event_types for k in KNOWN_EVENT_TYPES)))

        # --- tamper: event_data ------------------------------------------
        p3 = root / "three" / "chain.jsonl"
        c3 = AuditChain(p3)
        c3.append("agent_created", {"agent_name": "A"})
        c3.append("agent_created", {"agent_name": "B"})
        lines = _read(p3)
        lines[1]["event_data"]["agent_name"] = "HACKED"
        _rewrite(p3, lines)
        r3 = AuditChain(p3).verify()
        checks.append(("tamper event_data: detected", not r3.ok))
        checks.append(("tamper event_data: reason=entry_hash mismatch", r3.reason == "entry_hash mismatch"))
        checks.append(("tamper event_data: broken at seq=1", r3.broken_at_seq == 1))

        # --- tamper: prev_hash -------------------------------------------
        p4 = root / "four" / "chain.jsonl"
        c4 = AuditChain(p4)
        c4.append("agent_created", {"x": 1})
        c4.append("agent_created", {"x": 2})
        lines = _read(p4)
        lines[2]["prev_hash"] = "0" * 64
        _rewrite(p4, lines)
        r4 = AuditChain(p4).verify()
        checks.append(("tamper prev_hash: detected", not r4.ok))
        checks.append(("tamper prev_hash: reason=prev_hash mismatch", r4.reason == "prev_hash mismatch"))

        # --- tamper: seq gap ---------------------------------------------
        p5 = root / "five" / "chain.jsonl"
        c5 = AuditChain(p5)
        c5.append("agent_created", {"x": 1})
        lines = _read(p5)
        forged = dict(lines[-1])
        forged["seq"] = 99
        with p5.open("a", encoding="utf-8") as f:
            f.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")
        r5 = AuditChain(p5).verify()
        checks.append(("seq gap: detected", not r5.ok))
        checks.append(("seq gap: reason mentions gap", "seq gap" in (r5.reason or "")))

        # --- tamper: garbage JSON line -----------------------------------
        p6 = root / "six" / "chain.jsonl"
        c6 = AuditChain(p6)
        c6.append("agent_created", {"x": 1})
        with p6.open("a", encoding="utf-8") as f:
            f.write("{not valid json\n")
        r6 = AuditChain(p6).verify()
        checks.append(("garbage json: detected", not r6.ok))
        checks.append(("garbage json: reason mentions invalid JSON", "invalid JSON" in (r6.reason or "")))

        # --- round-trip: to_json_line + read_all -------------------------
        entries = c1.read_all()
        checks.append(("read_all: count matches appends + genesis", len(entries) == 5))
        checks.append(("read_all: seqs are 0..N", [e.seq for e in entries] == list(range(5))))
        # to_json_line roundtrip:
        line = entries[1].to_json_line()
        parsed = json.loads(line)
        checks.append(("to_json_line: roundtrips", parsed["entry_hash"] == entries[1].entry_hash))
        checks.append(("to_json_line: canonical (no spaces)", ", " not in line and ": " not in line))

    # --- summary ---------------------------------------------------------
    failures = [n for n, ok in checks if not ok]
    width = max(len(n) for n, _ in checks)
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}")
    print()
    print(f"{len(checks) - len(failures)}/{len(checks)} checks passed")
    if failures:
        print(f"FAILED: {failures}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
