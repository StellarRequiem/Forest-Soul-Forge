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

Single-writer at the in-process level: ``append()`` holds an internal RLock
(B199), so two threads racing the same chain instance can't fork the seq
sequence even if neither caller acquired ``app.state.write_lock`` first.
That lock remains the cross-resource serializer (chain + registry +
plugin filesystem must all advance together) but the chain's own
integrity is no longer hostage to caller discipline.

Concurrent appends from separate **processes** to the same JSONL file
remain undefined behavior; that's an OS-level fcntl-flock problem
deferred per ADR-0005 § threat-model. The internal lock here covers the
common case (one daemon process, multiple async tasks / threads).
"""
from __future__ import annotations

import hashlib
import json
import threading
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
    "forge_tool_uninstalled",  # B212 — DELETE /tools/installed
    "forge_skill_proposed",
    "forge_skill_installed",
    "forge_skill_uninstalled",  # B212 — DELETE /skills/installed
    "agent_tool_granted",       # B219 / ADR-0060 — runtime catalog-tool grant added
    "agent_tool_revoked",       # B219 / ADR-0060 — runtime catalog-tool grant revoked
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
    # ADR-003X Phase C1 — per-agent encrypted secrets store. Four
    # event types track the secret lifecycle without ever exposing
    # the value:
    #   - secret_set: operator wrote a new secret for an agent
    #   - secret_revealed: a tool decrypted a secret for use; pairs
    #     with the next tool_call_dispatched to show where it went
    #   - secret_blocked: agent tried to read a name not on its
    #     constitutional allowlist; structural refusal
    #   - secret_revoked: operator deleted a secret
    "secret_set",
    "secret_revealed",
    "secret_blocked",
    "secret_revoked",
    # ADR-003X K1 — verified-memory tier (Iron Gate equivalent).
    # Memory entries can be promoted from unverified to verified by
    # an external human verifier. Layered on consent grants via the
    # 'operator:verified' sentinel recipient — no schema change.
    #   - memory_verified: an entry was promoted to verified status
    #   - memory_verification_revoked: verification was withdrawn
    "memory_verified",
    "memory_verification_revoked",
    # ADR-003X K2 — operator-emitted ceremony events. Distinct from
    # tool-emitted events because the EMITTER is a human, not an
    # agent. Used to mark milestones, identity events, governance
    # decisions that don't fit any tool call. The 'ceremony_name' in
    # event_data is the operator-chosen label (e.g. 'iron_gate',
    # 'first_birth', 'tier_promotion').
    "ceremony",
    # ADR-003Y Y1 — conversation runtime CRUD lifecycle. Emitted by
    # the conversations router. Domain isolation and bridge-visibility
    # work depends on these being categorized — bridged turns are the
    # main exfiltration risk per ADR-003Y §threat-model.
    "conversation_started",
    "conversation_archived",
    "conversation_status_changed",
    "conversation_bridged",
    "conversation_participant_joined",
    "conversation_participant_left",
    "conversation_turn",
    "conversation_summarized",  # Y7 — body purged after retention window
    "retention_policy_changed",
    "ambient_nudge",            # Y5 — proactive agent turn (opt-in + rate-gated)
    # ADR-0036 — Verifier Loop. The Verifier agent emits one of these
    # per scan run with the candidate-pairs-considered + classifications
    # + flags-written counts in event_data. Operators auditing the
    # Verifier's track record (§4.2) start from this event type.
    #   - verifier_scan_completed: a Verifier completed a scan pass
    "verifier_scan_completed",
    # ADR-0056 E5 (Burst 191) — operator decision on a Smith cycle.
    # One event covers approve / deny / counter via the action
    # field in event_data. event_data also carries cycle_id,
    # branch, head_sha, the optional note, and (for approve) the
    # list of approved requested_tools. Pairs with the
    # tool_call_succeeded events for memory_tag_outcome.v1 +
    # tools_add invocations that the decision triggers
    # downstream — operators querying 'what happened to
    # cycle-N?' get the full picture by ORing this event type
    # with the standard tool-call lifecycle events.
    "experimenter_cycle_decision",
    # ADR-0054 T4 (Burst 181) — procedural-shortcut substitution.
    # Emitted INSTEAD of the dispatched + succeeded pair when the
    # dispatcher's ProceduralShortcutStep matches a stored
    # situation→action shortcut. A shortcut isn't a tool execution —
    # the underlying tool (typically llm_think.v1) never ran — so a
    # distinct event type makes the substitution explicitly visible
    # rather than burying it in metadata on a tool_call_succeeded.
    # Operators querying "what did this agent do?" need to OR
    # tool_call_succeeded with tool_call_shortcut for the complete
    # picture; that's deliberate so the difference between an
    # LLM-mediated answer and a recorded-pattern answer stays
    # legible. event_data carries: tool_key, instance_id, session_id,
    # shortcut_id, shortcut_similarity, shortcut_action_kind,
    # args_digest, result_digest, tokens_used, call_count.
    "tool_call_shortcut",
    # B199 (2026-05-08) — verifier KNOWN_EVENT_TYPES drift fix. The
    # following events have been emitted to the chain by recent ADRs
    # but the allowlist wasn't updated, so AuditChain.verify() was
    # logging them as forward-compat warnings. They are first-class
    # events; the warning was noise, not a real signal.
    #
    # ADR-0033 — plugin runtime lifecycle:
    "plugin_installed",
    # ADR-0041 — set-and-forget orchestrator scheduled-task lifecycle.
    # Five entries: pre-dispatch, post-dispatch (success), post-dispatch
    # (failure), and the two breaker transitions. Mirrors the tool-call
    # lifecycle shape so an auditor can reconstruct any agent's actions
    # from the chain alone, regardless of whether the action was an
    # operator-initiated tool call or a scheduler-initiated one.
    "scheduled_task_dispatched",
    "scheduled_task_completed",
    "scheduled_task_failed",
    "scheduled_task_circuit_breaker_tripped",
    "scheduled_task_circuit_breaker_reset",
    # ADR-0045 — agent posture / trust-light system. Posture transitions
    # are governance state changes, not tool calls; distinct event so the
    # audit trail records *who* changed posture and *when*.
    "agent_posture_changed",
    # ADR-0034 — SW-track triune. Out-of-triune attempts are governance
    # refusals (an agent in a triune tried to act outside its quorum
    # constraint). Recorded so operators see the structural refusal vs.
    # an ordinary tool_call_refused.
    "out_of_triune_attempt",
    # Hardware binding — local-first identity events. Emitted by the
    # hardware-binding subsystem when the daemon's machine fingerprint
    # changes (mismatch), is established (bound), or is intentionally
    # cleared (unbound). Pairs with priv_client / migration flows.
    "hardware_bound",
    "hardware_mismatch",
    "hardware_unbound",
    # ADR-0053 — per-tool plugin grants. An operator granting a plugin
    # tool to an agent is a constitutional change (the agent's allowed
    # toolset just expanded); recorded distinctly from agent_created.
    "agent_plugin_granted",
    # ADR-0048 — computer-control allowance. Operator relaxing a
    # governance constraint at runtime (e.g. enabling a side-effect tool
    # for one session). One event per relaxation so the trail captures
    # what was loosened, by whom, and against which agent.
    "governance_relaxed",
    # ADR-0056 E2 — experimenter ModeKitClampStep. The experimenter
    # agent's three modes (explore/work/display) carry different tool
    # caps; this event fires when the operator (or the agent itself
    # during a mode transition) sets the active cap.
    "task_caps_set",
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


@dataclass(frozen=True)
class ForkScanResult:
    """Output of :meth:`AuditChain.scan_for_forks` — exhaustive walk that
    reports EVERY structural anomaly in the chain, unlike :meth:`verify`
    which stops at the first.

    Two distinct anomaly classes:

    * ``duplicate_seqs`` — sequence numbers that appear in more than one
      entry. Signature of a write race where two threads grabbed the
      same ``self._head`` and both wrote with ``seq = head.seq + 1``.
      The pre-B199 forks at chain seqs 3728 / 3735-3738 / 3740 are the
      canonical example.

    * ``hash_mismatches`` — entries whose ``entry_hash`` doesn't match
      the SHA-256 of their own canonical-form payload. This is either
      (a) an external editor hand-mutating a line, (b) a writer using
      stale canonical-form code (B134 spec drift class), or (c) bit
      rot. Distinct from duplicate_seqs which is an in-process race.

    ``ok`` is True iff both lists are empty. A chain with duplicate
    seqs but no hash mismatches still has ``ok=False`` — the duplicate
    is the corruption.
    """

    ok: bool
    entries_scanned: int
    duplicate_seqs: tuple[int, ...] = ()
    hash_mismatches: tuple[int, ...] = ()
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
        # B199: internal append lock. RLock so a writer that re-enters
        # append() (e.g. genesis-on-init from a constructor running on
        # the same thread, or a future refactor that does prev=head /
        # head=append(...) inside the same call site) doesn't deadlock.
        # See module docstring for why this lives here vs. relying
        # solely on ``app.state.write_lock`` at every call site.
        self._append_lock = threading.RLock()
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

    def tail(self, n: int) -> list[ChainEntry]:
        """Return the most recent ``n`` entries (newest first).

        Reads the canonical JSONL from disk so runtime events are visible
        immediately — the registry's ``audit_events`` table only mirrors
        what's ingested at lifespan startup, so ``/audit/tail`` going
        through the registry would never see live dispatch / delegation
        events. Per ADR-0006, the JSONL is the source of truth and the
        registry is a derived index; tailing the source is the right
        primary path. Indexed by-agent / by-dna queries still live on
        the registry where the index actually helps.

        Memory bound is O(``n``) — uses a deque to keep only the last
        ``n`` parsed entries regardless of chain size. Malformed lines
        are skipped silently (consistent with :meth:`_recompute_head`);
        :meth:`verify` is the right tool for detecting structural breaks.
        """
        from collections import deque

        if n <= 0:
            return []
        keepers: deque[ChainEntry] = deque(maxlen=n)
        with self.path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    keepers.append(_entry_from_dict(obj))
                except (json.JSONDecodeError, AuditChainError):
                    # Tolerant — verify() reports structural breaks.
                    continue
        return list(reversed(keepers))

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
        adequate for the in-process threat model).

        B199 (2026-05-08, ADR audit `2026-05-08-chain-fork-incident.md`):
        the read-of-head + compute-of-hash + write-of-line + advance-of-head
        sequence is now wrapped in ``self._append_lock``. Without this,
        two threads could both read ``self._head`` before either advanced
        it, both compute ``next_seq = prev.seq + 1`` against the same
        prev_hash, both call ``_write_line``, and both set ``self._head``
        to *their* entry — leaving the chain on disk with two entries
        sharing the same seq + prev_hash and only the loser's entry
        actually link-reachable from head. That is exactly the fork
        signature observed at seqs 3728/3735-3738/3740 in the live chain.

        ``app.state.write_lock`` remains the cross-resource serializer
        (chain + registry + plugin filesystem must move together) — this
        lock is purely about chain self-consistency.
        """
        if not isinstance(event_type, str) or not event_type:
            raise InvalidAppendError("event_type must be a non-empty string")
        data = dict(event_data or {})  # defensive copy — caller mutations don't leak in

        with self._append_lock:
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

    def scan_for_forks(self) -> ForkScanResult:
        """Walk the entire chain and report EVERY structural anomaly.

        Sister of :meth:`verify`. The difference is short-circuit
        behavior: ``verify()`` stops at the first problem (correct
        for "is this chain still trustworthy" — one break is sufficient
        signal); ``scan_for_forks()`` reports all of them (correct for
        "where are all the breaches" — needed to remediate completely).

        Two anomaly classes:

        1. ``duplicate_seqs`` — sequence numbers appearing more than
           once. Pre-B199 race condition signature (see audits/
           2026-05-08-chain-fork-incident.md).
        2. ``hash_mismatches`` — entries whose ``entry_hash`` doesn't
           match SHA-256 of canonical form. Tampering or canonical-form
           drift (B134 class).

        Unknown event types are reported separately, as in :meth:`verify`.
        ``ok`` is True iff both anomaly lists are empty.
        """
        seen_seqs: dict[int, int] = {}        # seq → first lineno
        duplicate_seqs: list[int] = []
        hash_mismatches: list[int] = []
        unknown: list[str] = []
        count = 0

        try:
            file_handle = self.path.open("r", encoding="utf-8")
        except FileNotFoundError:
            return ForkScanResult(
                ok=False, entries_scanned=0,
            )

        with file_handle as f:
            for lineno, raw in enumerate(f):
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    entry = _entry_from_dict(obj)
                except (json.JSONDecodeError, AuditChainError):
                    # Malformed lines aren't fork signatures per se —
                    # verify() already covers structural breakage. The
                    # fork scan focuses on the two specific classes
                    # listed in the docstring. Skip malformed lines to
                    # keep walking; verify() is the right tool when
                    # the question is "is the chain valid?".
                    continue
                count += 1

                # Duplicate seq detection
                if entry.seq in seen_seqs:
                    duplicate_seqs.append(entry.seq)
                else:
                    seen_seqs[entry.seq] = lineno

                # Hash mismatch detection — recompute canonical form
                # and compare. Independent of seq linkage; an entry
                # with a valid prev_hash but wrong entry_hash still
                # surfaces here.
                expected_hash = _sha256_hex(_canonical_hash_input(
                    seq=entry.seq,
                    prev_hash=entry.prev_hash,
                    agent_dna=entry.agent_dna,
                    event_type=entry.event_type,
                    event_data=entry.event_data,
                ))
                if entry.entry_hash != expected_hash:
                    hash_mismatches.append(entry.seq)

                if entry.event_type not in KNOWN_EVENT_TYPES:
                    unknown.append(entry.event_type)

        # De-duplicate the duplicate_seqs list itself so the result is
        # clean (a triple-collision shouldn't show up twice).
        return ForkScanResult(
            ok=(not duplicate_seqs and not hash_mismatches),
            entries_scanned=count,
            duplicate_seqs=tuple(sorted(set(duplicate_seqs))),
            hash_mismatches=tuple(sorted(set(hash_mismatches))),
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
