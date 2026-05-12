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

    # Burst 111 (ADR-0043 follow-up): merged MCP registry view (YAML
    # base + plugin overrides). The dispatcher computes this once
    # before pipeline.run() so :class:`McpPerToolApprovalStep` can
    # consult per-tool ``requires_human_approval`` settings without
    # each step re-merging. ``None`` when the dispatcher's
    # ``plugin_runtime`` is unwired (test contexts) — the step
    # short-circuits to GO in that case.
    mcp_registry: dict[str, Any] | None = None

    # ADR-0045 T1 (Burst 114): per-agent posture (traffic light).
    # Populated by the dispatcher BEFORE pipeline.run() from the
    # agents.posture column. Consumed by PostureGateStep at the end
    # of the pipeline. ``None`` when the agent isn't in the registry
    # (test contexts) — the step short-circuits to GO in that case.
    # See ADR-0045 §"Dispatcher integration" for the gate semantics.
    agent_posture: str | None = None

    # ADR-0045 T3 (Burst 115): per-grant trust_tier view. Maps
    # plugin_name → trust_tier for active grants on this agent.
    # Populated alongside agent_posture. Consumed by PostureGateStep
    # ONLY when the dispatched tool is mcp_call.v1 (the per-grant
    # tier is plugin-specific, not agent-wide). ``None`` when no
    # plugin_grants table is wired (test contexts).
    #
    # Pre-ADR-0053 T4 this was the SOLE input PostureGateStep used
    # for plugin-grant tier lookup. Post-T4 (B239) the step prefers
    # ``plugin_grant_lookup_fn`` when present (specificity-wins per-
    # tool resolution); this flat view is kept as a fallback for
    # legacy contexts that don't wire the lookup function.
    plugin_grants_view: dict[str, str] | None = None

    # ADR-0053 T4 (Burst 239): specificity-wins plugin-grant
    # resolver. Callable(plugin_name: str, tool_name: str | None)
    # → trust_tier | None. When set, PostureGateStep prefers this
    # over the flat ``plugin_grants_view`` for mcp_call dispatches
    # because it resolves per-tool grants per ADR-0053 D3
    # (per-tool overrides plugin-level when the dispatched tool
    # has its own grant). ``None`` in test contexts that don't
    # wire the dispatcher's resolver — the step then falls back
    # to the flat view.
    plugin_grant_lookup_fn: Any = None  # Callable[[str, str | None], str | None] | None

    # ADR-0054 T3 (Burst 180): pre-computed procedural-shortcut match.
    # The dispatcher resolves this BEFORE running the pipeline because
    # embed_situation + search_by_cosine are async and the pipeline is
    # sync. ``None`` when no shortcut substrate is wired, the master
    # switch is off, eligibility gates fail, the prompt has no high-
    # confidence stored shortcut, or any pre-resolution path raised.
    # When set, ``ProceduralShortcutStep`` (placed last) converts the
    # tuple into a SHORTCUT terminal verdict that the dispatcher
    # branches on to substitute the recorded action without firing
    # llm_think. See ``ToolDispatcher._resolve_shortcut_match``.
    shortcut_match: Any = None  # tuple[ProceduralShortcut, float] | None

    # ADR-0056 E2 (Burst 188): experimenter mode tag. Threaded from
    # task_caps.mode through the dispatcher into here. ModeKitClampStep
    # reads it to clamp the experimenter agent's eligible tools per
    # mode (explore = read_only-only; work = full kit; display = tight
    # review-only allowlist). 'none' (default) is a no-op for every
    # agent; non-experimenter agents ignore the field entirely.
    mode: str = "none"

    # -- accumulated state (steps populate as the pipeline runs) ------------
    tool: Any = None
    resolved: Any = None  # _ResolvedToolConstraints from dispatcher.py
    posture_notes: list[str] = field(default_factory=list)
    active_model: str | None = None

    # ADR-0060 T2 (B220): when ConstraintResolutionStep used a runtime
    # grant rather than the constitution to resolve this tool, it sets
    # ``granted_via="catalog_grant"`` and ``grant_seq`` to the source
    # row's granted_at_seq. The dispatcher annotates
    # tool_call_dispatched with these fields so an auditor can tell
    # "this dispatch came from a runtime grant, not the constitution."
    # None on the constitution path.
    granted_via: str | None = None
    grant_seq: int | None = None
    # ADR-0060 T4 (B221): the grant's trust_tier, threaded through so
    # PostureGateStep can apply the posture × tier interaction matrix
    # (ADR-0060 D4). None unless granted_via is set. Values:
    # "green" | "yellow" | "red".
    granted_trust_tier: str | None = None

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
      - ``"GO"``       — proceed to the next step (or, after the last
                         step, to the dispatcher's execute leg).
      - ``"REFUSE"``   — terminal. Dispatcher emits ``tool_call_refused``
                         and returns :class:`DispatchRefused`.
      - ``"PENDING"``  — terminal. Dispatcher emits
                         ``tool_call_pending_approval`` and returns
                         :class:`DispatchPendingApproval`.
      - ``"SHORTCUT"`` — terminal. ADR-0054 T3 (Burst 180). Dispatcher
                         substitutes the recorded action_payload from
                         the matched ProceduralShortcut row instead of
                         firing the underlying tool. Emits dispatched
                         + succeeded events with shortcut_applied=True
                         metadata; T4 will graduate to a dedicated
                         ``tool_call_shortcut`` event type.

    Reason / detail / gate_source / side_effects /
    shortcut_candidate / shortcut_similarity are all optional;
    different terminal kinds populate different subsets.
    """

    verdict: str
    reason: str | None = None
    detail: str | None = None
    gate_source: str | None = None
    side_effects: str | None = None
    # ADR-0054 T3 (Burst 180): SHORTCUT verdict carries the matched
    # ProceduralShortcut + cosine score. Typed as ``Any`` to keep
    # this module free of a registry-tables import (governance_pipeline
    # is in the dependency floor; tables sit above it).
    shortcut_candidate: Any = None  # ProceduralShortcut | None
    shortcut_similarity: float | None = None

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

    @classmethod
    def shortcut(cls, candidate: Any, similarity: float) -> "StepResult":
        """ADR-0054 T3 (Burst 180) — terminal verdict carrying a
        matched procedural shortcut.

        Caller (dispatcher) is responsible for substituting the
        recorded action_payload + emitting the audit pair + calling
        record_match() on the table. Step itself is purely a
        verdict-converter — the heavy lifting (embed + search) ran
        before the pipeline started.
        """
        return cls(
            verdict="SHORTCUT",
            shortcut_candidate=candidate,
            shortcut_similarity=similarity,
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

    @property
    def is_shortcut(self) -> bool:
        return self.verdict == "SHORTCUT"


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

    ADR-0060 T2 (B220) addition: when the constitution lookup misses
    and a ``catalog_grant_lookup_fn`` was wired, the step consults
    it as a fallback. An active grant resolves to catalog-default
    constraints with ``applied_rules=("granted_via:catalog_grant",)``
    and the step tags ``dctx.granted_via`` + ``dctx.grant_seq`` for
    downstream audit annotation. The constitution path stays unchanged;
    grants are an augmentation, not a mutation.
    """

    load_resolved_constraints_fn: Any  # callable(Path, name, version) -> _ResolvedToolConstraints | None
    # ADR-0060 T2 — optional grant lookup. Signature:
    #   (instance_id, tool_name, tool_version) -> tuple[_ResolvedToolConstraints, int] | None
    # Returns (resolved_constraints, granted_at_seq) on grant hit, None otherwise.
    # When None (default), behavior is identical to pre-B220.
    catalog_grant_lookup_fn: Any = None

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        resolved = self.load_resolved_constraints_fn(
            dctx.constitution_path, dctx.tool_name, dctx.tool_version,
        )
        if resolved is None:
            # ADR-0060 T2: before refusing, check for an active runtime
            # grant. constitution_missing wins over grant lookup — a
            # missing constitution file is a registry / disk issue that
            # the operator must fix; a grant can't paper over it.
            if not dctx.constitution_path.exists():
                return StepResult.refuse(
                    "constitution_missing",
                    f"constitution.yaml not found at {dctx.constitution_path}",
                )
            if self.catalog_grant_lookup_fn is not None:
                grant_result = self.catalog_grant_lookup_fn(
                    dctx.instance_id, dctx.tool_name, dctx.tool_version,
                )
                if grant_result is not None:
                    granted_resolved, grant_seq, grant_tier = grant_result
                    dctx.resolved = granted_resolved
                    dctx.granted_via = "catalog_grant"
                    dctx.grant_seq = grant_seq
                    dctx.granted_trust_tier = grant_tier
                    return StepResult.go()
            return StepResult.refuse(
                "tool_not_in_constitution",
                (
                    f"agent's constitution does not list {dctx.key} — "
                    "re-birth or add via /agents/{instance_id}/tools/grant to grant access"
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


_INITIATIVE_ORDER: tuple[str, ...] = ("L0", "L1", "L2", "L3", "L4", "L5")


def _initiative_index(level: str) -> int:
    """Strictness index of an initiative level. Unknown → strictest
    (L0 = 0). Same fail-closed shape as the side-effects tier helper.
    """
    try:
        return _INITIATIVE_ORDER.index(level)
    except ValueError:
        return 0


@dataclass
class InitiativeFloorStep:
    """ADR-0021-amendment §5 — runtime check on the L0–L5 initiative
    ladder, orthogonal to the side-effects ceiling that
    :class:`GenreFloorStep` enforces.

    Where ``GenreFloorStep`` answers "how destructive can the agent's
    actions be?", this step answers "how autonomous is the agent
    allowed to be in deciding to act?"

    v0.2 enforcement is **opt-in per tool**: a tool that declares a
    ``required_initiative_level`` class attribute (e.g. ``"L4"``) is
    gated against the agent's ``initiative_level``. Tools that don't
    declare are unaffected. This avoids silent regressions while the
    catalog is audited tool-by-tool (deferred per-tool annotation
    work; v0.3 candidates).

    The agent's level is loaded from its constitution.yaml via
    ``initiative_loader_fn``. v0.2 reads the YAML on every dispatch;
    v0.3 may cache.
    """

    initiative_loader_fn: Any  # callable(constitution_path: Path) -> str

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        # Tool-side declaration. Tools without the attribute are no-op
        # for this gate — no enforcement until they opt in.
        required = getattr(dctx.tool, "required_initiative_level", "") or ""
        if not required:
            return StepResult.go()
        # Agent-side level from constitution.yaml. Defensive default
        # is L5 (no ceiling) so a missing/unreadable constitution
        # produces a permissive call rather than a hard refusal —
        # ConstraintResolutionStep already refuses missing
        # constitutions earlier in the pipeline, so reaching this
        # step with L5 means the constitution exists but lacks
        # the field (pre-amendment artifact).
        agent_level = self.initiative_loader_fn(dctx.constitution_path)
        if _initiative_index(required) <= _initiative_index(agent_level):
            return StepResult.go()
        return StepResult.refuse(
            "initiative_floor_violated",
            (
                f"tool {dctx.key} requires initiative_level >= {required}; "
                f"agent's level is {agent_level}. The agent's genre caps "
                f"its autonomy posture below this tool's requirement; "
                f"either operator-initiate the call (planned escape "
                f"hatch — not yet wired in v0.2) or change the role "
                f"to one with a higher initiative_level."
            ),
        )


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
class McpPerToolApprovalStep:
    """ADR-0043 Burst 111 — per-tool ``requires_human_approval``
    mirroring for plugin-contributed MCP servers.

    Only fires for ``mcp_call.v1`` dispatches. Reads
    ``dctx.mcp_registry[server_name]["requires_human_approval_per_tool"]``
    (the merged YAML+plugin view the dispatcher prebuilds before the
    pipeline runs) and, on a per-tool True, mutates
    ``dctx.resolved.constraints["requires_human_approval"]`` to True.

    Why mutate resolved.constraints rather than emit PENDING here:
    the downstream :class:`ApprovalGateStep` already knows how to
    elevate based on that constraint and emits the
    ``tool_call_pending_approval`` event with consistent
    ``gate_source`` semantics. Forcing the constraint upstream keeps
    a single source of approval truth — the alternative (a second
    PENDING-emitting step) would produce two parallel gate paths
    that would drift over time.

    The applied_rules log gets a ``mcp_per_tool_approval`` entry so
    the audit chain captures the per-tool override even though the
    final gate_source comes through as ``constraint``. Operators
    inspecting a pending ticket see both signals.

    Step is a no-op when:
      - tool_name is not ``mcp_call`` (the per-tool map is mcp-specific)
      - dctx.mcp_registry is None (plugin runtime unwired in tests)
      - args don't carry server_name + tool_name (validation step
        would have refused already, but defensive)
      - the resolved constraints are missing (no resolved tool —
        upstream lookup or constitution check should have refused)
    """

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        if dctx.tool_name != "mcp_call":
            return StepResult.go()
        registry = dctx.mcp_registry
        if not registry:
            return StepResult.go()
        if dctx.resolved is None:
            return StepResult.go()
        # mcp_call.v1's args validator guarantees these are non-empty
        # strings IF the validation step has fired — but
        # McpPerToolApprovalStep sits AFTER validation, so trust the
        # types here.
        server_name = dctx.args.get("server_name")
        tool_name = dctx.args.get("tool_name")
        if not isinstance(server_name, str) or not isinstance(tool_name, str):
            return StepResult.go()
        server_cfg = registry.get(server_name)
        if not isinstance(server_cfg, dict):
            return StepResult.go()
        per_tool_map = server_cfg.get("requires_human_approval_per_tool")
        if not isinstance(per_tool_map, dict):
            return StepResult.go()
        if not per_tool_map.get(tool_name, False):
            return StepResult.go()

        # Per-tool override fires. Force the resolved constraint to
        # True and tag the applied_rules so the audit trail records
        # WHICH per-tool entry triggered.
        constraints = dict(dctx.resolved.constraints)
        constraints["requires_human_approval"] = True
        # Replace the resolved object's constraints. _ResolvedToolConstraints
        # is a dataclass; using dataclass-style assignment works on
        # both frozen=False and post-replace flows.
        try:
            dctx.resolved.constraints = constraints
        except (AttributeError, TypeError):
            # Frozen dataclass / immutable shape — fall back to dict
            # mutation if the underlying object exposes a settable
            # constraints dict. Best-effort; the assertion below
            # catches the case where neither path worked.
            try:
                dctx.resolved.constraints.clear()
                dctx.resolved.constraints.update(constraints)
            except Exception:
                pass

        # Append to applied_rules if it exists. Different
        # _ResolvedToolConstraints implementations carry it as either
        # a list or a tuple; coerce to list before appending.
        applied = list(getattr(dctx.resolved, "applied_rules", ()))
        applied.append(
            f"mcp_per_tool_approval[{server_name}.{tool_name}]"
        )
        try:
            dctx.resolved.applied_rules = applied
        except (AttributeError, TypeError):
            pass

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


@dataclass
class PostureGateStep:
    """ADR-0045 T1 (Burst 114) — per-agent posture (traffic light).

    Outermost gate in the governance pipeline. Sits AFTER
    :class:`ApprovalGateStep` so it can override upstream GO verdicts
    with REFUSE (red) or PENDING (yellow) for non-read-only tools.

    Posture semantics:
      green:  honor existing per-tool / per-genre / per-grant policy
              as-is. Step adds no override → propagate upstream verdict.
      yellow: force ``pending_approval`` on every dispatch with
              ``side_effects != read_only``, regardless of per-tool
              config. The "I'm watching" mode.
      red:    refuse every dispatch with ``side_effects != read_only``
              outright. The "agent on probation" mode.

    Read-only tools (memory_recall, code_read, llm_think, etc.) pass
    through regardless of posture — the agent can still think and
    inspect even when its action authority is paused.

    Per-grant trust_tier (Burst 115 / ADR-0045 T3) folds in here when
    the dispatched tool is ``mcp_call.v1``. Precedence is
    red-dominates: the strongest signal across (agent posture,
    per-grant tier) wins. Burst 114 implements agent-only enforcement;
    Burst 115 layers per-grant on top.

    **ADR-0048 T5 (B160) — soulux-computer-control coverage.** This
    gate is the load-bearing safety surface for the Persistent
    Assistant's computer-control tools (per ADR-0048 Decision 4):

      - ``computer_screenshot.v1`` + ``computer_read_clipboard.v1``
        (side_effects=read_only) — pass through any posture, including
        red. The assistant can always *see*; that's not the dangerous
        capability.
      - ``computer_click.v1`` + ``computer_type.v1`` +
        ``computer_run_app.v1`` (side_effects=external) — yellow
        elevates to PENDING; red refuses outright.
      - ``computer_launch_url.v1`` (side_effects=network) — same.

    No new code is required for ADR-0048 T5: the gate's side_effects-
    based logic was correct from B114/B115. T5 is therefore a doc +
    test-coverage commit (B160) that confirms the substrate covers
    the new tool surface. When ADR-0048 T2/T3 land actual computer-
    control tools, they automatically inherit posture clamps with no
    additional gate code — the substrate "just works" because it
    operates on side_effects, not tool name.

    No-op when:
      - dctx.agent_posture is None (test contexts; agent not registered)
      - dctx.tool is None (upstream lookup step refused)
      - tool.side_effects == 'read_only' (always allowed)
      - upstream verdict already terminal (we don't double-handle —
        but we sit AFTER the upstream step that returns the verdict,
        so by the time we run, GO is the only state we see)
    """

    # T3 (Burst 115) wires this; T1 leaves it false.
    enforce_per_grant: bool = False

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        posture = dctx.agent_posture
        if posture is None:
            # Test contexts (no agent_registry wired) — skip
            # posture entirely.
            return StepResult.go()
        if dctx.tool is None:
            return StepResult.go()
        # Resolve effective side_effects (constitutional override or
        # tool default). Read-only ALWAYS bypasses posture — even a
        # red-postured agent can think + read.
        side_effects = (
            (dctx.resolved.side_effects if dctx.resolved else None)
            or dctx.tool.side_effects
        )
        if side_effects == "read_only":
            return StepResult.go()

        # Compute effective posture per ADR-0045 §"Interaction with
        # per-grant trust_tier":
        #
        #   1. Default to agent posture.
        #   2. If this is an mcp_call.v1 dispatch AND there's a
        #      grant for the specific server, FOLD in the per-grant
        #      tier per the precedence rule below.
        #   3. Resolve to a final action: GO / PENDING / REFUSE.
        #
        # Precedence rule: red dominates > yellow > green, EXCEPT
        # for the special downgrade case where agent-yellow +
        # grant-green for THIS plugin = ungated for this mcp_call
        # (operator explicitly extended trust for this server).
        # Red on either side dominates regardless of the other.
        effective_posture = posture
        if (
            self.enforce_per_grant
            and dctx.tool_name == "mcp_call"
        ):
            server_name = dctx.args.get("server_name")
            sub_tool_name = dctx.args.get("tool_name")
            grant_tier: str | None = None
            if isinstance(server_name, str):
                # ADR-0053 T4 (B239) preferred path: ask the
                # dispatcher's specificity-wins resolver. It returns
                # the per-tool grant's tier when one exists for the
                # exact (plugin, tool) being dispatched, else the
                # plugin-level grant's tier, else None.
                if dctx.plugin_grant_lookup_fn is not None:
                    grant_tier = dctx.plugin_grant_lookup_fn(
                        server_name,
                        sub_tool_name if isinstance(sub_tool_name, str) else None,
                    )
                # Legacy fallback for contexts that don't wire the
                # resolver (test fixtures pre-B239). Plugin-level only.
                elif dctx.plugin_grants_view is not None:
                    grant_tier = dctx.plugin_grants_view.get(server_name)
            if grant_tier is not None:
                rank = {"green": 0, "yellow": 1, "red": 2}
                agent_rank = rank.get(posture, 0)
                grant_rank = rank.get(grant_tier, 0)
                # Special downgrade: agent yellow + grant green = green
                # for THIS mcp_call. Operator explicitly vouched for
                # this plugin (or for the exact tool inside it, per
                # ADR-0053 D3), so ungate it.
                if posture == "yellow" and grant_tier == "green":
                    effective_posture = "green"
                else:
                    # Otherwise red-dominates: stronger signal wins.
                    effective_posture = (
                        grant_tier if grant_rank > agent_rank else posture
                    )

        # ADR-0060 T4 (B221): catalog-grant trust_tier interaction
        # matrix. When the dispatch came from a runtime grant
        # (granted_via="catalog_grant"), apply the matrix directly
        # per ADR-0060 D4. The agent's posture sets the floor; the
        # grant's tier shifts the threshold above that floor.
        #
        # | Agent  | green grant | yellow grant | red grant |
        # |--------|-------------|--------------|-----------|
        # | green  | GO          | GO           | GO        |
        # | yellow | GO          | GO           | PENDING   |
        # | red    | PENDING     | PENDING      | REFUSE    |
        #
        # Rationale: the operator who granted this specific tool
        # explicitly signaled trust at the grant's tier. A green-tier
        # grant means "I trust this exact (tool, agent) combination
        # fully," which is enough to override the agent's own posture
        # for THIS tool only. A red-tier grant on a red-posture agent
        # is the doubly-defended-against case — operator and agent
        # both flagged for review — so it refuses outright.
        #
        # This bypasses the agent-level branching below; the matrix
        # is the complete decision for grant-sourced dispatches.
        if dctx.granted_trust_tier is not None:
            grant_tier = dctx.granted_trust_tier
            if effective_posture == "green":
                # All grants allowed on a green agent.
                return StepResult.go()
            if effective_posture == "yellow":
                if grant_tier == "red":
                    return StepResult.pending(
                        gate_source="posture_yellow_grant_red",
                        side_effects=side_effects,
                    )
                return StepResult.go()
            if effective_posture == "red":
                if grant_tier == "red":
                    return StepResult.refuse(
                        "agent_posture_red_grant_red",
                        (
                            f"agent posture is RED and grant trust_tier is "
                            f"red — doubly-defended refusal. Raise either "
                            f"posture or grant tier to release."
                        ),
                    )
                # red agent + green/yellow grant: requires approval
                return StepResult.pending(
                    gate_source="posture_red_grant_lower",
                    side_effects=side_effects,
                )
            # Unknown posture value — fall through to legacy branching
            # below as a safe default.

        if effective_posture == "red":
            return StepResult.refuse(
                "agent_posture_red",
                (
                    f"agent posture is RED — non-read-only dispatch "
                    f"refused. Operator must raise the posture before "
                    f"this agent can act."
                ),
            )
        if effective_posture == "yellow":
            return StepResult.pending(
                gate_source="posture_yellow",
                side_effects=side_effects,
            )
        return StepResult.go()


@dataclass
class ProceduralShortcutStep:
    """ADR-0054 T3 (Burst 180) — fast-path bypass via procedural memory.

    Sits LAST in the pipeline. Reads the dispatcher-pre-computed
    :attr:`DispatchContext.shortcut_match`; on a non-None match,
    returns a ``SHORTCUT`` terminal verdict that the dispatcher
    branches on to substitute the recorded action_payload without
    firing the underlying tool (typically ``llm_think.v1``).

    Why the heavy lifting lives in the dispatcher rather than the
    step:

      The pipeline is sync (``evaluate(dctx) -> StepResult``), but
      ``embed_situation`` awaits ``provider.embed`` and
      ``search_by_cosine`` reads SQLite on the same connection the
      dispatcher already holds the write lock for. Async-ifying the
      whole pipeline would touch all 11 step classes for one async
      step's benefit. Pre-computing in the dispatcher (which is
      already async) and threading the result through dctx keeps the
      step protocol uniform AND the pipeline composition stable.

    This step's ONLY job is to convert a populated dctx field into a
    SHORTCUT verdict so the dispatcher's verdict switch sees it
    alongside REFUSE / PENDING / GO.

    Pipeline placement is LAST so:

      - All upstream gates fire first (hardware, args, constitution,
        posture, genre, counter, approval). A shortcut never bypasses
        governance — it only bypasses the LLM round-trip when the
        agent is already cleared to make this call.
      - The pre-resolution step also encodes posture in its
        eligibility: shortcuts only resolve when posture is green
        (or unset for tests). A yellow/red agent never sees a
        shortcut even if one would match — it goes through the LLM
        path so the operator-installed monitoring/refusal triggers
        fire normally.
    """

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        match = dctx.shortcut_match
        if match is None:
            return StepResult.go()
        # match shape: tuple[ProceduralShortcut, float (cosine)]
        try:
            candidate, similarity = match
        except (TypeError, ValueError):
            # Defensive: a future caller-bug that stuffs the wrong
            # shape into shortcut_match must NOT crash dispatch —
            # fall through to the normal path.
            return StepResult.go()
        return StepResult.shortcut(candidate, float(similarity))


# ---------------------------------------------------------------------------
# ModeKitClampStep — ADR-0056 E2 (Burst 188).
# ---------------------------------------------------------------------------
# Per-mode kit clamp for the experimenter agent. Reads
# task_caps.mode and refuses dispatches that don't fit the active
# mode's allowed-tool subset. No-op for any agent whose role isn't
# the configured experimenter_role (default 'experimenter').
#
# The step receives the mode + the calling agent's role through
# DispatchContext (E2 wires both into the dispatcher's per-call
# ctx assembly). The step's eligibility check is fast-path:
#
#   1. If ``dctx.role != self.experimenter_role`` → GO. Other
#      agents are unaffected by mode tagging.
#   2. If ``dctx.mode in (None, "", "none")`` → GO. Default
#      behavior: full kit. Operators dispatching against the
#      experimenter without a mode tag get the same surface as
#      any other actuator agent.
#   3. If ``dctx.mode == "explore"`` and tool side_effects is
#      not "read_only" → REFUSE with reason="mode_kit_clamp"
#      and detail naming the mode + the rejected tool.
#   4. If ``dctx.mode == "work"`` → GO. Work mode uses the full
#      kit; mode tag is informational + propagates into audit
#      events for the cycle-report system to pick up later
#      (E5 self-augmentation needs the mode lineage).
#   5. If ``dctx.mode == "display"`` → GO only when tool name
#      is in the tiny review-only allowlist (memory_recall,
#      memory_tag_outcome, git_diff_read, git_log_read,
#      audit_chain_verify). Refuse otherwise.
#   6. Unknown mode value → REFUSE with reason="mode_kit_clamp"
#      and detail="unknown mode: {mode!r}". Prevents typo'd
#      modes from silently degrading to default behavior.
#
# Pipeline placement: AFTER ConstraintResolutionStep (so we can
# read the resolved tool's side_effects) and BEFORE PostureGateStep
# (so the kit clamp applies before posture adjudicates the result).
# Both ProceduralShortcutStep + ModeKitClampStep live AFTER the
# core gates; ProceduralShortcutStep is LAST. ModeKitClampStep
# sits just before it.
@dataclass
class ModeKitClampStep:
    """ADR-0056 E2 (Burst 188) — per-mode kit clamp for the
    experimenter agent.

    Smith's three modes (explore / work / display) each restrict
    which tools fire. The clamp is an inline refuse rather than
    a per-mode constitution because:

    - Constitutions are immutable per agent (ADR-0001 D2). Smith
      can't have THREE constitutions; it has one with broad kit,
      and the clamp narrows the dispatch surface dynamically.
    - Operators set the mode at dispatch time
      (`task_caps.mode=explore` etc.). A constitution rewrite
      would require re-birth.
    - The pipeline already supports per-call refusal via
      :class:`StepResult.refuse`, and the audit chain captures
      the refusal with the mode_kit_clamp reason for after-the-
      fact analysis.

    No-op for any agent whose role isn't ``experimenter_role``
    (default 'experimenter'). Other agents on the dispatcher
    inherit zero behavior change.
    """

    experimenter_role: str = "experimenter"

    # Display mode allowlist — review-only tools the operator
    # uses to inspect Smith's past cycles without granting any
    # action surface. Keep this list TIGHT; widening it weakens
    # the display-mode contract.
    DISPLAY_ALLOWED_TOOLS: tuple[str, ...] = (
        "memory_recall",
        "memory_tag_outcome",
        "git_diff_read",
        "git_log_read",
        "audit_chain_verify",
    )

    def evaluate(self, dctx: DispatchContext) -> StepResult:
        # 1. Off-experimenter pass-through.
        if dctx.role != self.experimenter_role:
            return StepResult.go()

        # 2. None / unset mode = full kit.
        mode = (dctx.mode or "none").strip().lower()
        if mode == "none":
            return StepResult.go()

        # Side_effects from resolved if available, else from the
        # tool's own declaration. Same precedence used in
        # GenreFloorStep + PostureGateStep — keeps the clamp
        # consistent with the rest of the pipeline.
        side_effects = (
            (dctx.resolved.side_effects if dctx.resolved else None)
            or (dctx.tool.side_effects if dctx.tool else "read_only")
        )
        tool_name = dctx.tool_name

        # 3. Explore — read-only only.
        if mode == "explore":
            if side_effects == "read_only":
                return StepResult.go()
            return StepResult.refuse(
                "mode_kit_clamp",
                (
                    f"experimenter is in mode='explore'; tool "
                    f"{tool_name!r} has side_effects={side_effects!r} "
                    f"(read_only required). Switch to mode='work' "
                    f"to dispatch mutating tools."
                ),
            )

        # 4. Work — full kit passthrough.
        if mode == "work":
            return StepResult.go()

        # 5. Display — tight review-only allowlist.
        if mode == "display":
            if tool_name in self.DISPLAY_ALLOWED_TOOLS:
                return StepResult.go()
            return StepResult.refuse(
                "mode_kit_clamp",
                (
                    f"experimenter is in mode='display' (review only); "
                    f"tool {tool_name!r} not in the display "
                    f"allowlist {self.DISPLAY_ALLOWED_TOOLS}. Switch "
                    f"to mode='work' to act, or mode='explore' to "
                    f"discover."
                ),
            )

        # 6. Unknown mode — refuse loudly so a typo doesn't
        #    silently degrade to default behavior.
        return StepResult.refuse(
            "mode_kit_clamp",
            (
                f"unknown experimenter mode {mode!r}; valid: "
                f"none, explore, work, display."
            ),
        )
