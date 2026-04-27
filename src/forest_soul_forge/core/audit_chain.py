"""Audit chain — append-only, hash-linked JSONL log of agent-affecting events.

v0.1 is **tamper-evident**, not tamper-proof. Threat model: operator-honest-
but-forgetful. A root attacker with write access to the file plus the builder
code can forge a valid chain — that class of threat is explicitly deferred.
See docs/decisions/ADR-0005-audit-chain.md.

Each entry is one JSON object per line:

    {"seq": N, "timestamp": "...", "prev_hash": "...", "entry_hash": "...",
     "agent_dna": "..." | null, "event_type": "...", "event_data": {...}}

The hash over *seq + prev_hash + agent_dna + event_type + event_data*
(canonical JSON, sort_keys, no whitespace) is stored as ``entry_hash``. The
next entry's ``prev_hash`` points at it. The chain links back to the literal
string ``"GENESIS"`` at seq=0.

Timestamps are **not** hashed — clock skew would otherwise break verification.
They're informational.

Single-writer assumption in v0.1. Concurrent appends from separate processes
are undefined behavior; documented rather than silently corrupt.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_SCHEMA_VERSION: int = 1

DEFAULT_CHAIN_PATH = Path("audit/chain.jsonl")

GENESIS_PREV_HASH: str = "GENESIS"
GENESIS_EVENT_TYPE: str = "chain_created"

# Enumerated event types known in v0.1. Unknown types are tolerated with a
# verification warning — forward-compat for the Phase 3 runtime to emit new
# event shapes without requiring the verifier to change.
KNOWN_EVENT_TYPES: frozenset[str] = frozenset({
    "chain_created",
    "agent_created",
    "agent_spawned",
    "agent_archived",
    "constitution_regenerated",
    # Per ADR-0017: re-running the soul.md `## Voice` renderer against
    # an existing agent. The agent's identity (dna, instance_id,
    # constitution_hash) is unchanged; only the Voice section and the
    # narrative_* frontmatter fields are rewritten.
    "voice_regenerated",
    "manual_override",
    "drift_detected",
    "finding_emitted",
    "policy_violation_detected",
    # ADR-0019 T2 — tool dispatch lifecycle. Five entries (rather than
    # one) so the chain itself records the moment between "we said yes"
    # and "the tool returned": a crash mid-execute leaves a `dispatched`
    # without a matching `succeeded`/`failed`, which is diagnostically
    # useful.
    "tool_call_dispatched",
    "tool_call_succeeded",
    "tool_call_refused",
    "tool_call_failed",
    "tool_call_pending_approval",
    # ADR-0019 T3 — approval queue lifecycle. Distinct from
    # `tool_call_refused` (the runtime auto-rejected) so an auditor can
    # tell "the runtime said no" from "the operator said no" without
    # parsing the reason field.
    "tool_call_approved",
    "tool_call_rejected",
    # ADR-0021 T6 — operator override on spawn-compat. Recorded so the
    # operator can later answer "why did we spawn this combination?".
    "spawn_genre_override",
    # ADR-0031 T2 — skill runtime lifecycle. Seven entries so the chain
    # records the moment-by-moment progression of a skill run; an
    # auditor can reconstruct the DAG walk from the chain alone.
    # skill_invoked at start, skill_completed at end (both with the
    # same skill_invoked_seq backref); per-step events in between
    # carry skill_invoked_seq + step_id so they group cleanly.
    "skill_invoked",
    "skill_step_started",
    "skill_step_completed",
    "skill_step_skipped",
    "skill_step_failed",
    "skill_completed",
    # ADR-0030 T1 / ADR-0031 T1 — forge lifecycle. Forge events are
    # emitted by the CLI (and future frontend) before the artifact
    # exists in the catalog; they record what the operator
    # considered, not just what got installed.
    "forge_tool_proposed",
    "forge_tool_installed",
    "forge_skill_proposed",
    "forge_skill_installed",
    # ADR-0022 v0.1 + ADR-0027 — memory lifecycle. memory_read is
    # only emitted for cross-agent reads (per-agent self-reads are
    # too noisy and the data is already in scope). bulk operations
    # decompose to per-entry events so an attacker can't hide a
    # thousand disclosures in a single audit line.
    "memory_written",
    "memory_read",
    "memory_disclosed",
    "memory_consent_granted",
    "memory_consent_revoked",
    "memory_deleted",
    "memory_purged",
    "memory_scope_override",
    # ADR-0033 — Security Swarm cross-agent invocation. delegate.v1
    # emits this when one agent invokes another agent's skill; the
    # event records caller, target, skill ref, and a one-line reason
    # so the chain captures every tier-crossing in the swarm.
    "agent_delegated",
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AuditChainError(Exception):
    """Base class for audit-chain failures."""


class InvalidAppendError(AuditChainError):
    """Caller tried to append something that would corrupt the chain."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ChainEntry:
    """One line from the audit chain.

    Mostly immutable — ``event_data`` is a plain ``dict`` so entries created
    from parsed JSON keep their structure. Callers should treat event_data as
    read-only.
    """

    seq: int
    timestamp: str
    prev_hash: str
    entry_hash: str
    agent_dna: str | None
    event_type: str
    event_data: dict[str, Any]

    def to_json_line(self) -> str:
        """Serialize this entry as one JSONL line (with trailing newline)."""
        payload = {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            "agent_dna": self.agent_dna,
            "event_type": self.event_type,
            "event_data": self.event_data,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


@dataclass(frozen=True)
class VerificationResult:
    """Output of :meth:`AuditChain.verify`.

    ``ok`` is False on the first structural break; ``broken_at_seq`` and
    ``reason`` point at the offending entry. Unknown event types don't flip
    ``ok`` — they're reported separately in ``unknown_event_types``.
    """

    ok: bool
    entries_verified: int
    broken_at_seq: int | None
    reason: str | None
    unknown_event_types: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Canonical hash input
# ---------------------------------------------------------------------------
def _canonical_hash_input(
    *,
    seq: int,
    prev_hash: str,
    agent_dna: str | None,
    event_type: str,
    event_data: dict[str, Any],
) -> bytes:
    """Return the UTF-8 bytes that go into entry_hash.

    Deliberately excludes ``timestamp`` (clock skew would otherwise corrupt
    verification) and ``entry_hash`` itself (self-reference is impossible).
    Kept in sync with the DNA / constitution canonicalization: JSON with
    ``sort_keys=True, separators=(",", ":")``.
    """
    payload = {
        "seq": seq,
        "prev_hash": prev_hash,
        "agent_dna": agent_dna,
        "event_type": event_type,
        "event_data": event_data,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# AuditChain
# ---------------------------------------------------------------------------
class AuditChain:
    """Append-only hash-chained JSONL audit log.

    Instantiating this class opens (or creates) the chain file. If the file
    doesn't exist, a ``chain_created`` genesis entry is written synchronously
    before the constructor returns — so every chain you hold has at least a
    genesis. Callers that want strict "don't create on open" semantics can
    check ``path.exists()`` before construction.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._head: ChainEntry | None = None
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch()
        self._head = self._recompute_head()
        if self._head is None:
            self._write_genesis()

    # ---- introspection --------------------------------------------------
    @property
    def head(self) -> ChainEntry | None:
        """Most recent entry, or ``None`` if the chain is empty.

        Right after construction the genesis entry makes ``head`` non-None.
        """
        return self._head

    def read_all(self) -> list[ChainEntry]:
        """Return every entry from seq=0 forward. Raises on malformed JSON."""
        entries: list[ChainEntry] = []
        with self.path.open("r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f):
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError as err:
                    raise AuditChainError(
                        f"malformed JSON at line {lineno + 1}: {err}"
                    ) from err
                entries.append(_entry_from_dict(obj))
        return entries

    # ---- mutation -------------------------------------------------------
    def append(
        self,
        event_type: str,
        event_data: dict[str, Any] | None = None,
        *,
        agent_dna: str | None = None,
    ) -> ChainEntry:
        """Append an event. Returns the committed :class:`ChainEntry`.

        Validates type, hashes the canonical form, links to the current head,
        and writes atomically *per line* (one write, one fsync-less flush —
        adequate for the v0.1 single-writer threat model).
        """
        if not isinstance(event_type, str) or not event_type:
            raise InvalidAppendError("event_type must be a non-empty string")
        data = dict(event_data or {})  # defensive copy — caller mutations don't leak in

        prev = self._head
        if prev is None:
            # Invariant: after __init__ the chain always has at least genesis.
            # If we land here it means the file was truncated out from under us.
            raise AuditChainError(
                "chain has no head; refusing to append (file may have been truncated externally)"
            )

        next_seq = prev.seq + 1
        prev_hash = prev.entry_hash
        entry_hash = _sha256_hex(_canonical_hash_input(
            seq=next_seq,
            prev_hash=prev_hash,
            agent_dna=agent_dna,
            event_type=event_type,
            event_data=data,
        ))
        entry = ChainEntry(
            seq=next_seq,
            timestamp=_now_iso(),
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            agent_dna=agent_dna,
            event_type=event_type,
            event_data=data,
        )
        self._write_line(entry)
        self._head = entry
        return entry

    # ---- verification ---------------------------------------------------
    def verify(self) -> VerificationResult:
        """Walk the chain from genesis forward, checking hashes and sequencing.

        Stops at the first structural problem and reports it. Unknown event
        types are recorded as warnings but don't flip ``ok`` — the chain can
        contain forward-compat entries from a later runtime version.
        """
        unknown: list[str] = []
        count = 0
        prev_entry: ChainEntry | None = None

        try:
            file_handle = self.path.open("r", encoding="utf-8")
        except FileNotFoundError:
            return VerificationResult(
                ok=False, entries_verified=0, broken_at_seq=None,
                reason="chain file missing",
            )
        with file_handle as f:
            for lineno, raw in enumerate(f):
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    return VerificationResult(
                        ok=False, entries_verified=count,
                        broken_at_seq=(prev_entry.seq + 1) if prev_entry else 0,
                        reason=f"invalid JSON at line {lineno + 1}",
                        unknown_event_types=tuple(sorted(set(unknown))),
                    )
                try:
                    entry = _entry_from_dict(obj)
                except AuditChainError as err:
                    return VerificationResult(
                        ok=False, entries_verified=count,
                        broken_at_seq=None,
                        reason=f"malformed entry at line {lineno + 1}: {err}",
                        unknown_event_types=tuple(sorted(set(unknown))),
                    )

                # Seq monotonicity
                expected_seq = 0 if prev_entry is None else prev_entry.seq + 1
                if entry.seq != expected_seq:
                    return VerificationResult(
                        ok=False, entries_verified=count,
                        broken_at_seq=entry.seq,
                        reason=f"seq gap: expected {expected_seq}, got {entry.seq}",
                        unknown_event_types=tuple(sorted(set(unknown))),
                    )

                # prev_hash linkage
                expected_prev = GENESIS_PREV_HASH if prev_entry is None else prev_entry.entry_hash
                if entry.prev_hash != expected_prev:
                    return VerificationResult(
                        ok=False, entries_verified=count,
                        broken_at_seq=entry.seq,
                        reason="prev_hash mismatch",
                        unknown_event_types=tuple(sorted(set(unknown))),
                    )

                # entry_hash recomputation
                expected_hash = _sha256_hex(_canonical_hash_input(
                    seq=entry.seq,
                    prev_hash=entry.prev_hash,
                    agent_dna=entry.agent_dna,
                    event_type=entry.event_type,
                    event_data=entry.event_data,
                ))
                if entry.entry_hash != expected_hash:
                    return VerificationResult(
                        ok=False, entries_verified=count,
                        broken_at_seq=entry.seq,
                        reason="entry_hash mismatch",
                        unknown_event_types=tuple(sorted(set(unknown))),
                    )

                # Unknown event type → warn, don't fail
                if entry.event_type not in KNOWN_EVENT_TYPES:
                    unknown.append(entry.event_type)

                prev_entry = entry
                count += 1

        return VerificationResult(
            ok=True, entries_verified=count,
            broken_at_seq=None, reason=None,
            unknown_event_types=tuple(sorted(set(unknown))),
        )

    # ---- internals ------------------------------------------------------
    def _recompute_head(self) -> ChainEntry | None:
        """Return the last successfully parsed entry in the chain.

        Tolerates malformed lines on a best-effort basis so that a tampered
        or partially-written file can still be *opened* — the only reliable
        signal that the chain is intact is :meth:`verify`, and refusing to
        construct would prevent callers from ever running that check. A
        malformed line does not advance the head, so verify() will still
        stop at the break.
        """
        last: ChainEntry | None = None
        with self.path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    last = _entry_from_dict(obj)
                except (json.JSONDecodeError, AuditChainError):
                    # Leave 'last' alone; verify() reports the structural break.
                    continue
        return last

    def _write_genesis(self) -> None:
        entry_hash = _sha256_hex(_canonical_hash_input(
            seq=0,
            prev_hash=GENESIS_PREV_HASH,
            agent_dna=None,
            event_type=GENESIS_EVENT_TYPE,
            event_data={"schema_version": AUDIT_SCHEMA_VERSION},
        ))
        entry = ChainEntry(
            seq=0,
            timestamp=_now_iso(),
            prev_hash=GENESIS_PREV_HASH,
            entry_hash=entry_hash,
            agent_dna=None,
            event_type=GENESIS_EVENT_TYPE,
            event_data={"schema_version": AUDIT_SCHEMA_VERSION},
        )
        self._write_line(entry)
        self._head = entry

    def _write_line(self, entry: ChainEntry) -> None:
        # Open in append mode per write so a process crash between calls
        # leaves the file in a consistent state (every line is either fully
        # present or absent). Not as tight as fsync, but matches the v0.1
        # threat model.
        with self.path.open("a", encoding="utf-8") as f:
            f.write(entry.to_json_line())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    """Return current UTC time as a fixed-width ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_from_dict(obj: dict[str, Any]) -> ChainEntry:
    required = ("seq", "timestamp", "prev_hash", "entry_hash", "event_type")
    for k in required:
        if k not in obj:
            raise AuditChainError(f"entry missing required field {k!r}")
    event_data = obj.get("event_data") or {}
    if not isinstance(event_data, dict):
        raise AuditChainError(f"event_data must be an object, got {type(event_data).__name__}")
    return ChainEntry(
        seq=int(obj["seq"]),
        timestamp=str(obj["timestamp"]),
        prev_hash=str(obj["prev_hash"]),
        entry_hash=str(obj["entry_hash"]),
        agent_dna=(str(obj["agent_dna"]) if obj.get("agent_dna") is not None else None),
        event_type=str(obj["event_type"]),
        event_data=event_data,
    )
