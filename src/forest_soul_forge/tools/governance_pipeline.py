"""Governance pipeline for tool dispatch — composable pre-execute checks.

Extracted from ``ToolDispatcher.dispatch()``'s inline if/elif chain per
the 2026-04-30 load-bearing survey. The single ``dispatch()`` method
had grown to 8 sequential pre-execute checks (hardware quarantine,
task usage cap, tool lookup, args validation, constraint resolution,
posture overrides, genre floor, call counter, approval gate) interleaved
with the resolved-state assembly that downstream branches depend on.

This module replaces that inline chain with a list of named
:class:`PipelineStep` objects driven by :class:`GovernancePipeline`.
The dispatcher builds the pipeline once at construction; each
``dispatch()`` call walks it via a small :class:`DispatchContext`
that accumulates resolved state (loaded tool, parsed constraints,
posture notes) as steps fire.

Why composable instead of inline:

- **ADR-003Y** conversation runtime needs to add a per-conversation
  rate-limit check. Adding a step is a 30-line drop-in vs. another
  if-clause inside an already-large method.
- **Test isolation.** Each step is a single-purpose class with a
  ``evaluate(dctx) -> StepResult`` method. Tests can drive steps
  in isolation; tests can also assemble a partial pipeline to
  exercise edge cases (e.g. "what happens if hardware quarantine
  fails before tool lookup" was previously hard to test cleanly).
- **Refusal/pending semantics are uniform.** Every step returns the
  same :class:`StepResult` shape. The dispatcher's branch on the
  pipeline outcome is a single switch over ``result.verdict``
  rather than scattered ``return self._refuse(...)`` / ``return
  self._pending_approval(...)`` calls inside each check.

Design constraints honored:

- **Public API of dispatcher unchanged.** ``ToolDispatcher.dispatch()``
  still returns ``DispatchSucceeded | DispatchRefused | DispatchPendingApproval | DispatchFailed``.
- **Same audit events in the same order.** Hardware-mismatch event
  from ``HardwareQuarantineStep``, task_caps_set event from
  ``TaskUsageCapStep``, posture_override_applied event from
  ``PostureOverrideStep`` all preserved with original payloads.
- **Single-writer SQLite discipline preserved.** The dispatcher's
  caller still holds the write lock; steps do not acquire locks.

Pipeline order matters. The order below mirrors the original
``dispatch()`` implementation; do not reorder without considering:

  1. ``HardwareQuarantineStep`` — refuses BEFORE registry lookup so
     a quarantined agent doesn't even surface ``unknown_tool``.
  2. ``TaskUsageCapStep`` — operator's per-task budget caps the
     dispatch even before tool lookup. Operator authority over
     tool-author authority on this axis.
  3. ``ToolLookupStep`` — resolves :attr:`DispatchContext.tool`.
     Steps after this point may rely on it.
  4. ``ArgsValidationStep`` — uses the loaded tool's ``validate``.
  5. ``ConstraintResolutionStep`` — reads constitution.yaml for
     this tool. Sets :attr:`DispatchContext.resolved`. Refuses on
     ``constitution_missing`` or ``tool_not_in_constitution``.
  6. ``PostureOverrideStep`` — applies per-model posture overrides
     (T2.2a). Mutates :attr:`DispatchContext.resolved` in place
     (replaces it with the tightened version).
  7. ``GenreFloorStep`` — symmetric runtime check vs ADR-0021 T5
     birth-time check.
  8. ``CallCounterStep`` — read-only check of
     ``max_calls_per_session``. Counter increment happens AFTER the
     pipeline, in the dispatcher's execute leg.
  9. ``ApprovalGateStep`` — last step. May return PENDING which
     terminates the pipeline cleanly.

If a future ADR needs a check between two of these (e.g. ADR-003Y's
per-conversation rate limit between counter and approval), insert a
new step at the right index in :meth:`ToolDispatcher.__post_init__`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.tools.base import (
    ToolError,
    ToolRegistry,
    ToolValidationError,
)


# ---------------------------------------------------------------------------
# DispatchContext — the call-scoped data each step reads / mutates.
#
# Attributes split into two groups:
#   - INPUTS: set once by the caller (dispatcher.dispatch); steps
#     should treat them as read-only.
#   - ACCUMULATED: populated by earlier steps; later steps consume.
# ---------------------------------------------------------------------------
@dataclass
class DispatchContext:
    """Mutable per-call context threaded through the pipeline.

    Steps read inputs and either set accumulated fields (e.g.
    ``ToolLookupStep`` sets :attr:`tool`) or return a terminal
    :class:`StepResult` that aborts the chain.
    """

    # -- inputs (caller sets at construction) -------------------------------
    instance_id: str
    agent_dna: str
    role: str
    genre: str | None
    session_id: str
    constitution_path: Path
    tool_name: str
    tool_version: str
    args: dict[str, Any]
    provider: Any = None
    task_caps: dict[str, Any] | None = None

    # -- accumulated state (steps populate as the pipeline runs) ------------
    tool: Any = None
    resolved: Any = None  # _ResolvedToolConstraints from dispatcher.py
    posture_notes: list[str] = field(default_factory=list)
    active_model: str | None = None

    @property
    def key(self) -> str:
        return f"{self.tool_name}.v{self.tool_version}"


# ---------------------------------------------------------------------------
# StepResult — uniform terminal/non-terminal verdict per step.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StepResult:
    """One step's verdict.

    ``verdict`` is one of:
      - ``"GO"``      — proceed to the next step (or, after the last
                        step, to the dispatcher's execute leg).
      - ``"REFUSE"``  — terminal. Dispatcher emits ``tool_call_refused``
                        and returns :class:`DispatchRefused`.
      - ``"PENDING"`` — terminal. Dispatcher emits
                        ``tool_call_pending_approval`` and returns
                        :class:`DispatchPendingApproval`.

    Reason / detail / gate_source / side_effects are all optional;
    different terminal kinds populate different subsets.
    """

    verdict: str
    reason: str | None = None
    detail: str | None = None
    gate_source: str | None = None
    side_effects: str | None = None

    @classmethod
    def go(cls) -> "StepResult":
        return cls(verdict="GO")

    @classmethod
    def refuse(cls, reason: str, detail: str) -> "StepResult":
        return cls(verdict="REFUSE", reason=reason, detail=detail)

    @classmethod
    def pending(cls, gate_source: str, side_effects: str) -> "StepResult":
        return cls(
            verdict="PENDING", gate_source=gate_source, side_effects=side_effects,
        )

    @property
    def terminal(self) -> bool:
        return self.verdict != "GO"

    @property
    def is_refuse(self) -> bool:
        return self.verdict == "REFUSE"

    @property
    def is_pending(self) -> bool:
        return self.verdict == "PENDING"


# ---------------------------------------------------------------------------
# PipelineStep protocol — one method, no required base class.
# ---------------------------------------------------------------------------
class PipelineStep(Protocol):
    """Single pre-execute check.

    Implementations should be small and single-purpose. Steps that
    need to emit audit events do so via dependencies passed at
    construction (typically ``audit: AuditChain``).

    The ``evaluate`` method returns a :class:`StepResult` — terminal
    results stop the pipeline, GO results let it continue.
    """

    def evaluate(self, dctx: DispatchContext) -> StepResult: ...


# ---------------------------------------------------------------------------
# GovernancePipeline — runs a list of steps until the first terminal
# verdict or end-of-list (in which case the verdict is GO).
# ---------------------------------------------------------------------------
@dataclass
class GovernancePipeline:
    """Ordered list of pre-execute checks.

    Constructed once per dispatcher, walked once per ``dispatch()``
    call. Steps fire in declaration order; the first terminal verdict
    short-circuits.
    """

    steps: list[PipelineStep]

    def run(self, dctx: DispatchContext) -> StepResult:
        """Walk the steps. Return the first terminal verdict or GO."""
        for step in self.steps:
            result = step.evaluate(dctx)
            if result.terminal:
                return result
        return StepResult.go()


# ---------------------------------------------------------------------------
# Step implementations.
#
# Each step takes its dependencies at construction, NOT through
# DispatchContext. Keeps the call-scoped context shape minimal and
# steps individually testable.
# ---------------------------------------------------------------------------

@dataclass
class HardwareQuarantineStep:
    """K6 hardware-binding quarantine check.

    Refuses BEFORE the rest of the pipeline runs so a quarantined
    agent's call doesn't surface any other failure mode (unknown_tool,
    bad_args) that could mask the real issue. Emits a
    ``hardware_mismatch`` audit event before returning REFUSE so the
    operator can see what tripped.

    Reads ``hardware_quarantine_reason_fn`` (injected) which inspects
    the constitution file. None means "not bound" or "binding
    matches" — both are GO.
    """

    audit: AuditChain
    quarantine_reason_fn: Any  # callable(Path) -> dict[str,str] | None

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        reason = self.quarantine_reason_fn(dctx.constitution_path)
        if reason is None:
            return StepResult.go()
        try:
            self.audit.append(
                "hardware_mismatch",
                {
                    "instance_id": dctx.instance_id,
                    "tool_key": dctx.key,
                    "session_id": dctx.session_id,
                    "expected_machine_fingerprint": reason["expected"],
                    "constitution_binding": reason["binding"],
                },
                agent_dna=dctx.agent_dna,
            )
        except Exception:
            # Audit-emit failure shouldn't mask the actual refusal.
            pass
        return StepResult.refuse(
            "hardware_quarantined",
            (
                f"agent {dctx.instance_id} is hardware-bound to "
                f"{reason['binding'][:8]}… but this machine is "
                f"{reason['expected'][:8]}…. "
                "Operator must POST /agents/{id}/hardware/unbind to release."
            ),
        )


@dataclass
class TaskUsageCapStep:
    """T2.2b operator-supplied per-task token budget.

    Operator-authored ``task_caps.usage_cap_tokens`` shorts the
    dispatch when the session has already consumed that many tokens.
    Pre-tool-lookup so the operator's authority overrides any
    tool-side decision.

    Also emits the ``task_caps_set`` audit event when task_caps
    are present (idempotent — emitter checks audit chain for prior
    occurrence per-session).
    """

    audit: AuditChain
    session_token_sum_fn: Any  # callable(instance_id, session_id) -> int
    task_caps_set_fn: Any  # callable(task_caps, instance_id, agent_dna, session_id, key) -> None

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        if not dctx.task_caps:
            return StepResult.go()
        # Side-effect: emit task_caps_set if it's the first time this
        # session has seen these caps. Idempotent emitter.
        try:
            self.task_caps_set_fn(
                dctx.task_caps,
                instance_id=dctx.instance_id,
                agent_dna=dctx.agent_dna,
                session_id=dctx.session_id,
                key=dctx.key,
            )
        except Exception:
            pass

        usage_cap = dctx.task_caps.get("usage_cap_tokens")
        if usage_cap and isinstance(usage_cap, int) and usage_cap > 0:
            used = self.session_token_sum_fn(dctx.instance_id, dctx.session_id)
            if used >= usage_cap:
                return StepResult.refuse(
                    "task_usage_cap_exceeded",
                    (
                        f"session {dctx.session_id} has consumed {used} tokens; "
                        f"operator-supplied usage_cap_tokens={usage_cap} "
                        "blocks further dispatches. Start a new session "
                        "or raise the cap."
                    ),
                )
        return StepResult.go()


@dataclass
class ToolLookupStep:
    """Registry lookup. Sets :attr:`DispatchContext.tool` on success."""

    registry: ToolRegistry

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        tool = self.registry.get(dctx.tool_name, dctx.tool_version)
        if tool is None:
            return StepResult.refuse(
                "unknown_tool",
                f"no tool registered for {dctx.key} "
                f"(registered: {list(self.registry.tools)})",
            )
        dctx.tool = tool
        return StepResult.go()


@dataclass
class ArgsValidationStep:
    """Calls ``tool.validate(args)`` and converts ToolError → REFUSE.

    Runs BEFORE the counter touches the DB so a typo doesn't burn
    budget.
    """

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        try:
            dctx.tool.validate(dctx.args)
        except ToolValidationError as e:
            return StepResult.refuse("bad_args", str(e))
        except ToolError as e:
            return StepResult.refuse("bad_args", str(e))
        return StepResult.go()


@dataclass
class ConstraintResolutionStep:
    """Read constitution.yaml for this tool's resolved constraints.

    Refuses if the constitution file is missing OR the tool isn't
    listed (different reasons; the dispatcher's caller may want to
    distinguish — e.g., trigger a registry rebuild on the former).
    """

    load_resolved_constraints_fn: Any  # callable(Path, name, version) -> _ResolvedToolConstraints | None

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        resolved = self.load_resolved_constraints_fn(
            dctx.constitution_path, dctx.tool_name, dctx.tool_version,
        )
        if resolved is None:
            if not dctx.constitution_path.exists():
                return StepResult.refuse(
                    "constitution_missing",
                    f"constitution.yaml not found at {dctx.constitution_path}",
                )
            return StepResult.refuse(
                "tool_not_in_constitution",
                (
                    f"agent's constitution does not list {dctx.key} — "
                    "re-birth or add via tools_add to grant access"
                ),
            )
        dctx.resolved = resolved
        return StepResult.go()


@dataclass
class PostureOverrideStep:
    """T2.2a per-model posture overrides — tightenings only.

    Reads constitution-level ``provider_posture_overrides`` keyed by
    active model. Replaces ``dctx.resolved`` with the tightened
    version. Emits ``posture_override_applied`` if any change happened.
    """

    audit: AuditChain
    resolve_active_model_fn: Any  # callable(provider) -> str | None
    apply_overrides_fn: Any  # callable(resolved, constitution_path, active_model) -> (resolved, notes)

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        active_model = self.resolve_active_model_fn(dctx.provider)
        dctx.active_model = active_model
        new_resolved, posture_notes = self.apply_overrides_fn(
            dctx.resolved, dctx.constitution_path, active_model,
        )
        dctx.resolved = new_resolved
        dctx.posture_notes = list(posture_notes or [])
        if posture_notes:
            try:
                self.audit.append(
                    "posture_override_applied",
                    {
                        "instance_id":   dctx.instance_id,
                        "tool_key":      dctx.key,
                        "session_id":    dctx.session_id,
                        "active_model":  active_model,
                        "tightenings":   posture_notes,
                    },
                    agent_dna=dctx.agent_dna,
                )
            except Exception:
                pass
        return StepResult.go()


@dataclass
class GenreFloorStep:
    """ADR-0019 T6 — runtime tier ceiling check.

    Symmetric with ADR-0021 T5's birth-time kit-tier enforcement.

    Note on the getter pattern: ``genre_engine_fn`` is a zero-arg
    callable that returns the *currently bound* GenreEngine. We can't
    capture the engine reference at step construction because tests
    (and the daemon's hot-reload) mutate ``dispatcher.genre_engine``
    after the dispatcher is built. The callable indirection means each
    ``evaluate`` re-reads the live binding.
    """

    genre_engine_fn: Any  # callable() -> GenreEngine | None
    check_genre_floor_fn: Any  # callable(engine, role, side_effects, provider) -> (ok, detail)

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        side_effects = (
            (dctx.resolved.side_effects if dctx.resolved else None)
            or dctx.tool.side_effects
        )
        ok, detail = self.check_genre_floor_fn(
            engine=self.genre_engine_fn(),
            role=dctx.role,
            tool_side_effects=side_effects,
            provider=dctx.provider,
        )
        if not ok:
            return StepResult.refuse("genre_floor_violated", detail or "")
        return StepResult.go()


@dataclass
class CallCounterStep:
    """Per-session ``max_calls_per_session`` pre-check (read-only).

    Counter INCREMENT happens AFTER the pipeline, in the dispatcher's
    execute leg, so a refused or pending call does not cost a slot.
    """

    counter_get_fn: Any  # callable(instance_id, session_id) -> int

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        max_calls = int(
            (dctx.resolved.constraints if dctx.resolved else {}).get(
                "max_calls_per_session", 0,
            ) or 0
        )
        current = int(self.counter_get_fn(dctx.instance_id, dctx.session_id))
        if max_calls and current >= max_calls:
            return StepResult.refuse(
                "max_calls_exceeded",
                (
                    f"session {dctx.session_id} has {current}/{max_calls} calls "
                    "used; further dispatches blocked until session reset"
                ),
            )
        return StepResult.go()


@dataclass
class ApprovalGateStep:
    """Decide pending_approval vs go.

    Two paths can elevate (OR):
      (a) the tool's resolved ``requires_human_approval`` constraint
      (b) the agent's genre policy (ADR-0033 A4 graduation):
            security_high  → any non-read_only call
            security_mid   → filesystem/external
            security_low   → no elevation (tool config wins)

    Audit metadata records WHICH path fired (constraint / genre /
    constraint+genre) so an operator inspecting the ticket can see
    which gate was responsible.
    """

    genre_requires_approval_fn: Any  # callable(genre, side_effects) -> bool

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        constraint_requires = bool(
            (dctx.resolved.constraints if dctx.resolved else {}).get(
                "requires_human_approval", False,
            )
        )
        side_effects = (
            (dctx.resolved.side_effects if dctx.resolved else None)
            or dctx.tool.side_effects
        )
        genre_requires = self.genre_requires_approval_fn(dctx.genre, side_effects)
        if constraint_requires or genre_requires:
            gate_source = (
                "constraint+genre" if (constraint_requires and genre_requires)
                else ("genre" if genre_requires else "constraint")
            )
            return StepResult.pending(gate_source=gate_source, side_effects=side_effects)
        return StepResult.go()
