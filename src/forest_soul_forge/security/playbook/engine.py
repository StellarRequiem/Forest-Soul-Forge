"""PlaybookEngine — detection-driven SOAR playbook resolver.

ADR-0066 T2 (B455). The engine owns the parsed playbook set and
matches every `detection_fired` event against it. For each matched
playbook within its cooldown window, the engine resolves the step
plan — per-step approval disposition + `${...}` argument
interpolation against the detection context — and emits one
`playbook_executed` audit chain event (ADR-0066 D5).

What the engine does NOT do: dispatch tools. Mirroring the
DetectionEngine — which evaluates rules and emits `detection_fired`
but never acts — the PlaybookEngine resolves + records the response
plan and leaves actual tool dispatch (kill a process, archive an
artifact) to the agent-runtime layer. A substrate engine holding a
ToolContext and the write_lock to fire `isolate_process` would be a
new, very wide trust surface; the audit-grade deliverable is the
`playbook_executed` record with each step's resolved approval
state. Live dispatch wiring is deferred exactly as the
DetectionEngine's daemon-lifespan wiring is deferred.

Per ADR-0066 D7, the engine refuses to come up if any playbook
fails to parse — `ready()` is the gate, `load_errors` the punch
list. This mirrors DetectionEngine.ready() / load_errors verbatim.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from forest_soul_forge.security.playbook.events import (
    PlaybookDef,
    PlaybookError,
)
from forest_soul_forge.security.playbook.parser import parse_playbooks_from_dir

if TYPE_CHECKING:
    from forest_soul_forge.core.audit_chain import AuditChain


log = logging.getLogger(__name__)

# ${...} interpolation marker. The v1 resolver substitutes
# ${playbook_id}, ${playbook_version} and ${detection.<dotted.path>}
# against the firing detection's event_data. An unresolved
# reference is left as the literal ${...} so a downstream dispatcher
# can see — and re-resolve — what the substrate could not.
_INTERP_RE = re.compile(r"\$\{([^}]+)\}")

# How far back poll_chain walks the chain tail looking for
# unprocessed detection_fired events. Matches the search_window the
# detection substrate's security reader uses.
_DEFAULT_SEARCH_WINDOW = 2000


@dataclass(frozen=True)
class PlaybookStepOutcome:
    """One step's resolved disposition inside a fired playbook."""

    id: str
    action: str
    requires_approval: bool
    approval_state: str           # "auto_approved" | "pending_approval"
    resolved_args: dict[str, Any]


@dataclass(frozen=True)
class PlaybookRunResult:
    """One playbook fired by one detection.

    `outcome` follows the ADR-0066 D5 enum:
      - "approval_pending" — at least one step needs operator
        approval; the playbook is recorded but not all steps can
        proceed unattended.
      - "completed" — every step auto-approved; the engine resolved
        and recorded the full plan. (At the substrate layer
        "completed" means the engine finished its resolve-and-record
        job; actual tool dispatch is the agent-runtime layer.)
      - "halted" — reserved for a resolve-time error.
    """

    playbook_id: str
    playbook_version: str
    trigger_detection_seq: int | None
    trigger_rule_id: str
    target_entity: str
    steps: tuple[PlaybookStepOutcome, ...]
    outcome: str
    audit_event_seq: int | None


@dataclass(frozen=True)
class PlaybookProcessResult:
    """Per-detection summary — what every playbook did with one
    `detection_fired` event. Cheap inspection surface for tests +
    the harness that does not require walking the chain."""

    detection_seq: int | None
    rule_id: str
    severity: str
    playbooks_matched: int
    runs: tuple[PlaybookRunResult, ...]
    cooldown_skipped: tuple[str, ...]   # playbook_ids suppressed by cooldown


class PlaybookEngine:
    """Holds the playbook set + resolves detections against it.

    Hot-reload is via `reload_from_dir()`. Reload swaps the playbook
    set atomically under self._lock; a mid-resolve reload waits.

    Construction NEVER raises on a single bad playbook — the
    `load_errors` list carries (Path, PlaybookError) tuples so the
    operator gets the full punch list. `ready()` is the gate: the
    engine refuses to resolve (returns an empty result) when
    load_errors is non-empty.
    """

    def __init__(
        self,
        playbooks_dir: Path | None = None,
        *,
        # Pre-loaded playbooks (used in tests; production goes
        # through the playbooks_dir path).
        playbooks: Iterable[PlaybookDef] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._playbooks: tuple[PlaybookDef, ...] = ()
        self.load_errors: list[tuple[Path, PlaybookError]] = []
        self.playbooks_dir = playbooks_dir
        # Cooldown high-water marks, keyed by the ADR-0066 D4
        # fingerprint (playbook_id, rule_id, target_entity) → the
        # wall-clock time the playbook last fired for that target.
        self._cooldowns: dict[tuple[str, str, str], float] = {}
        if playbooks is not None:
            with self._lock:
                self._playbooks = tuple(playbooks)
        elif playbooks_dir is not None:
            self.reload_from_dir(playbooks_dir)

    @property
    def playbooks(self) -> tuple[PlaybookDef, ...]:
        with self._lock:
            return self._playbooks

    def ready(self) -> bool:
        """True iff zero load errors. Per ADR-0066 D7 a single bad
        playbook blocks the whole engine — the operator repairs the
        file before the engine resumes."""
        return not self.load_errors

    def reload_from_dir(self, directory: Path) -> None:
        """Re-parse + atomically swap the playbook set.

        Per ADR-0066 D7, if ANY playbook fails to parse, the engine
        retains the previous set and records the failures in
        load_errors. Silent fall-through to an incomplete set would
        mask drift. Mirrors DetectionEngine.reload_from_dir.
        """
        parsed, failed = parse_playbooks_from_dir(directory)
        with self._lock:
            if failed:
                self.load_errors = list(failed)
                log.warning(
                    "PlaybookEngine: reload refused — %d failure(s) "
                    "(previous set retained)", len(failed),
                )
                return
            self.load_errors = []
            self._playbooks = tuple(parsed)
            log.info(
                "PlaybookEngine: loaded %d playbook(s) from %s",
                len(self._playbooks), directory,
            )

    # ----- detection processing -------------------------------------------

    def process_detection(
        self,
        detection: dict[str, Any],
        *,
        detection_seq: int | None = None,
        audit_chain: "AuditChain | None" = None,
        agent_dna: str | None = None,
        now: float | None = None,
    ) -> PlaybookProcessResult:
        """Resolve every playbook against one `detection_fired`
        event's `event_data` dict.

        `detection` carries the keys the DetectionEngine emits:
        rule_id, severity, matched_event_ids, batch_id, technique,
        rule_version. `detection_seq` is the chain seq of the
        detection_fired entry — recorded as the D5
        `trigger_detection_id`.

        For each playbook whose trigger matches and whose cooldown
        window is clear, the engine resolves the step plan and emits
        one `playbook_executed` chain event. `audit_chain` is
        OPTIONAL — when None the engine still resolves and returns
        the runs so tests can inspect without standing up a chain
        (same posture as DetectionEngine.scan).
        """
        wall = time.time() if now is None else now
        rule_id = str(detection.get("rule_id") or "")
        severity = str(detection.get("severity") or "")
        target = _target_entity(detection)

        if not self.ready() or not rule_id or not severity:
            return PlaybookProcessResult(
                detection_seq=detection_seq,
                rule_id=rule_id,
                severity=severity,
                playbooks_matched=0,
                runs=(),
                cooldown_skipped=(),
            )

        with self._lock:
            playbooks = self._playbooks

        runs: list[PlaybookRunResult] = []
        cooldown_skipped: list[str] = []
        matched = 0

        # Deterministic order — ADR-0066 open-question default:
        # sequential by playbook_id alphabetical when several
        # playbooks fire on the same detection.
        for pb in sorted(playbooks, key=lambda p: p.playbook_id):
            if not pb.trigger.matches(rule_id=rule_id, severity=severity):
                continue
            matched += 1

            fingerprint = (pb.playbook_id, rule_id, target)
            if self._cooldown_blocks(fingerprint, pb.trigger.cooldown_seconds, wall):
                cooldown_skipped.append(pb.playbook_id)
                continue

            run = self._resolve_run(
                pb,
                detection=detection,
                detection_seq=detection_seq,
                target=target,
                audit_chain=audit_chain,
                agent_dna=agent_dna,
            )
            runs.append(run)
            # Register the cooldown high-water mark only after the
            # playbook actually fired.
            with self._lock:
                self._cooldowns[fingerprint] = wall

        return PlaybookProcessResult(
            detection_seq=detection_seq,
            rule_id=rule_id,
            severity=severity,
            playbooks_matched=matched,
            runs=tuple(runs),
            cooldown_skipped=tuple(cooldown_skipped),
        )

    def poll_chain(
        self,
        audit_chain: "AuditChain",
        *,
        since_seq: int = 0,
        search_window: int = _DEFAULT_SEARCH_WINDOW,
    ) -> tuple[int, list[PlaybookProcessResult]]:
        """Tail the audit chain for `detection_fired` events newer
        than `since_seq` and process each in chain order.

        This is the ADR-0066 "subscribe to detection_fired events
        (chain tail)" surface. Returns (new_high_water_seq, results)
        — the caller persists the high-water seq and passes it back
        on the next poll so a detection is processed exactly once.

        Cooldown ordering depends on chronological processing, so
        the events are sorted oldest-first before resolution even
        though tail() yields newest-first.
        """
        entries = audit_chain.tail(search_window)
        fired = []
        for e in entries:
            if getattr(e, "event_type", None) != "detection_fired":
                continue
            seq = getattr(e, "seq", None)
            if not isinstance(seq, int) or seq <= since_seq:
                continue
            fired.append(e)
        fired.sort(key=lambda e: e.seq)

        results: list[PlaybookProcessResult] = []
        high_water = since_seq
        for e in fired:
            data = getattr(e, "event_data", None) or {}
            results.append(self.process_detection(
                data, detection_seq=e.seq, audit_chain=audit_chain,
            ))
            high_water = max(high_water, e.seq)
        return (high_water, results)

    # ----- internals ------------------------------------------------------

    def _cooldown_blocks(
        self,
        fingerprint: tuple[str, str, str],
        cooldown_seconds: int,
        now: float,
    ) -> bool:
        """ADR-0066 D4 — a playbook does NOT re-fire for the same
        (playbook, rule, target) within its cooldown window. A
        cooldown of 0 never blocks."""
        if cooldown_seconds <= 0:
            return False
        with self._lock:
            last = self._cooldowns.get(fingerprint)
        if last is None:
            return False
        return (now - last) < cooldown_seconds

    def _resolve_run(
        self,
        pb: PlaybookDef,
        *,
        detection: dict[str, Any],
        detection_seq: int | None,
        target: str,
        audit_chain: "AuditChain | None",
        agent_dna: str | None,
    ) -> PlaybookRunResult:
        context = {
            "playbook_id": pb.playbook_id,
            "playbook_version": pb.playbook_version,
            "detection": detection,
        }
        outcomes: list[PlaybookStepOutcome] = []
        for step in pb.steps:
            outcomes.append(PlaybookStepOutcome(
                id=step.id,
                action=step.action,
                requires_approval=step.requires_approval,
                approval_state=(
                    "pending_approval" if step.requires_approval
                    else "auto_approved"
                ),
                resolved_args=_interpolate(step.args, context),
            ))

        any_pending = any(o.requires_approval for o in outcomes)
        outcome = "approval_pending" if any_pending else "completed"

        audit_seq = self._emit_playbook_executed(
            pb, outcomes, detection_seq, outcome, audit_chain, agent_dna,
        )

        return PlaybookRunResult(
            playbook_id=pb.playbook_id,
            playbook_version=pb.playbook_version,
            trigger_detection_seq=detection_seq,
            trigger_rule_id=str(detection.get("rule_id") or ""),
            target_entity=target,
            steps=tuple(outcomes),
            outcome=outcome,
            audit_event_seq=audit_seq,
        )

    def _emit_playbook_executed(
        self,
        pb: PlaybookDef,
        outcomes: list[PlaybookStepOutcome],
        detection_seq: int | None,
        outcome: str,
        audit_chain: "AuditChain | None",
        agent_dna: str | None,
    ) -> int | None:
        """Emit the ADR-0066 D5 `playbook_executed` event.

        Each step record carries the D5 key set. `audit_event_seq`
        and `executed_at` are null at the substrate layer — they are
        filled by a future dispatching layer; recording the keys now
        keeps the event shape forward-compatible. `status` mirrors
        `approval_state` because, absent dispatch, the step's status
        IS its approval disposition.
        """
        if audit_chain is None:
            return None
        try:
            entry = audit_chain.append(
                pb.postcondition_audit_event_type,
                {
                    "playbook_id":          pb.playbook_id,
                    "playbook_version":     pb.playbook_version,
                    "trigger_detection_id": detection_seq,
                    "steps": [
                        {
                            "id":              o.id,
                            "action":          o.action,
                            "status":          o.approval_state,
                            "approval_state":  o.approval_state,
                            "audit_event_seq": None,
                            "executed_at":     None,
                        }
                        for o in outcomes
                    ],
                    "outcome":   outcome,
                    "fired_at":  time.time(),
                },
                agent_dna=agent_dna,
            )
            seq = getattr(entry, "seq", None)
            return seq if isinstance(seq, int) else None
        except Exception as e:
            # Chain emission failure is logged but does NOT roll
            # back the resolved run — the result still carries it so
            # callers can inspect. Same store-first / anchor-second
            # posture the DetectionEngine uses.
            log.warning(
                "PlaybookEngine: chain append failed for playbook "
                "%r: %r", pb.playbook_id, e,
            )
            return None


def _target_entity(detection: dict[str, Any]) -> str:
    """Derive the ADR-0066 D4 cooldown target — the primary subject
    of the detection.

    ADR-0066 D4 specs `target_entity` as "whichever the detection
    emits (process pid, file path, user id)". The DetectionEngine's
    current `detection_fired` payload does not carry an explicit
    target field, so the resolver is defensive:
      1. an explicit `target_entity` key if a future detection
         shape adds one;
      2. else the first matched event id (a stable per-scenario
         proxy — re-fires on the same event collapse under cooldown);
      3. else the batch id;
      4. else "" (cooldown then keys on (playbook, rule) alone).
    """
    explicit = detection.get("target_entity")
    if isinstance(explicit, str) and explicit:
        return explicit
    matched = detection.get("matched_event_ids")
    if isinstance(matched, (list, tuple)) and matched:
        return str(matched[0])
    batch = detection.get("batch_id")
    if isinstance(batch, str) and batch:
        return batch
    return ""


def _interpolate(value: Any, context: dict[str, Any]) -> Any:
    """Resolve ${...} references in a value, recursing into dicts
    and lists. A reference that cannot be resolved is left as the
    literal ${...} string — an honest signal to a downstream
    dispatcher that the substrate could not bind it."""
    if isinstance(value, str):
        def _sub(m: "re.Match[str]") -> str:
            resolved = _lookup(m.group(1).strip(), context)
            return m.group(0) if resolved is None else str(resolved)
        return _INTERP_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _interpolate(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, context) for v in value]
    return value


def _lookup(path: str, context: dict[str, Any]) -> Any:
    """Resolve a dotted path against the interpolation context.
    Returns None when any segment is missing — the caller then
    leaves the literal reference in place."""
    cur: Any = context
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur
