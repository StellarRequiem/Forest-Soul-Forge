"""``ToolDispatcher`` — fast-path tool runtime (ADR-0019 T2).

Turns a tool-call request into a governed dispatch:

    request → registry lookup → validate args → load resolved
    constraints from agent's constitution.yaml → check counter →
    decide path (run | approval | refuse) → execute → audit + count

Three exit paths:

* **succeeded** — tool ran, returned a :class:`ToolResult`. Audit emits
  ``tool_call_dispatched`` then ``tool_call_succeeded`` (split entries
  so a crash mid-execute leaves a clear "we attempted X, never finished"
  signal).
* **refused** — tool was rejected before execution. Reasons:
  ``unknown_tool``, ``bad_args``, ``max_calls_exceeded``,
  ``side_effects_exceed_budget`` (T6 hook), ``unknown_agent``,
  ``constitution_missing``. Audit emits ``tool_call_refused`` only.
* **pending_approval** — tool needs human go-ahead per the resolved
  constraints. T2 issues a stub ticket and emits
  ``tool_call_pending_approval``; T3 will turn the ticket into a real
  queue entry the operator can approve via the frontend.

Side-effect ladder (read_only < network < filesystem < external) is
NOT enforced in T2 — that's T6 (genre runtime enforcement). T2 trusts
the catalog/genre check that already happened at /birth and /spawn.
The dispatcher does enforce ``requires_human_approval`` and
``max_calls_per_session`` because those are per-call decisions the
build-time check can't make.

Single-writer SQLite discipline: callers must hold the daemon's write
lock before invoking ``dispatch``. The dispatcher both reads the
counter and writes counter + audit-chain entries; concurrent dispatches
without external serialization corrupt the count and the chain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
)
from forest_soul_forge.tools.governance_pipeline import (
    ApprovalGateStep,
    ArgsValidationStep,
    CallCounterStep,
    InitiativeFloorStep,
    ConstraintResolutionStep,
    DispatchContext,
    GenreFloorStep,
    GovernancePipeline,
    HardwareQuarantineStep,
    McpPerToolApprovalStep,
    ModeKitClampStep,
    PostureGateStep,
    PostureOverrideStep,
    ProceduralShortcutStep,
    StepResult,
    TaskUsageCapStep,
    ToolLookupStep,
)


# ---------------------------------------------------------------------------
# Audit event types — 5 total, hash-chained like every other event.
# Listed here so future tranches see the full set; the AuditChain's
# KNOWN_EVENT_TYPES set is forward-compat (tolerates unknown types with a
# warning) but the canonical list lives next to its emitter.
# ---------------------------------------------------------------------------
EVENT_DISPATCHED = "tool_call_dispatched"
EVENT_SUCCEEDED = "tool_call_succeeded"
EVENT_REFUSED = "tool_call_refused"
EVENT_FAILED = "tool_call_failed"
EVENT_PENDING_APPROVAL = "tool_call_pending_approval"
# ADR-0019 T3 — operator decision events. Emitted by the resume path
# (approve) and the reject path; the underlying tool dispatch then
# re-uses EVENT_DISPATCHED + EVENT_SUCCEEDED/EVENT_FAILED for the
# replay so an auditor can trace the full lifecycle:
#   pending_approval → approved → dispatched → succeeded
#   pending_approval → rejected   (no replay; tool never ran)
EVENT_APPROVED = "tool_call_approved"
EVENT_REJECTED = "tool_call_rejected"
# ADR-0054 T4 (Burst 181) — single audit event emitted instead of the
# dispatched + succeeded pair when ProceduralShortcutStep matches a
# stored situation→action shortcut. A shortcut isn't a tool execution
# so a distinct event type makes the substitution explicitly visible
# rather than buried in metadata. event_data carries: tool_key,
# instance_id, session_id, shortcut_id, shortcut_similarity,
# shortcut_action_kind, args_digest, result_digest, tokens_used,
# call_count, side_effects, applied_rules.
EVENT_SHORTCUT = "tool_call_shortcut"


# ---------------------------------------------------------------------------
# Outcome dataclasses — one shape per exit path so the endpoint can
# pattern-match on .__class__ instead of inspecting a status string.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DispatchSucceeded:
    """Tool ran. ``result`` is what the agent receives back.

    ``call_count_after`` is the post-increment counter so an operator
    inspecting the dispatch outcome can see budget usage at a glance.
    """

    tool_key: str
    result: ToolResult
    call_count_after: int
    audit_seq: int


@dataclass(frozen=True)
class DispatchRefused:
    """Tool was rejected without execution. ``reason`` is one of:

    - ``unknown_tool``
    - ``bad_args``
    - ``max_calls_exceeded``
    - ``side_effects_exceed_budget`` (T6 hook, not raised in T2)
    - ``unknown_agent``
    - ``constitution_missing``
    - ``tool_not_in_constitution``
    - ``unexpected_exception``

    ``detail`` is operator-facing prose. ``audit_seq`` points at the
    refusal entry in the chain.
    """

    tool_key: str
    reason: str
    detail: str
    audit_seq: int


@dataclass(frozen=True)
class DispatchPendingApproval:
    """Tool needs human approval. T2 issues a stub ticket; T3 makes it real.

    ``ticket_id`` is a synthetic ID derived from
    ``(instance_id, session_id, audit_seq)`` so re-running this tool
    while the same ticket is still pending is observable. The frontend
    picks up the ticket via ``/agents/{id}/pending_calls`` (T3).
    """

    tool_key: str
    ticket_id: str
    side_effects: str
    audit_seq: int


@dataclass(frozen=True)
class DispatchFailed:
    """Tool started executing but crashed mid-flight. ``exception_type``
    is the class name; the full traceback isn't carried in this object
    (it lives in the audit entry's ``event_data.traceback_fingerprint``)
    because dispatch outcomes are returned to the agent and we don't
    want unhandled exception text leaking through that path."""

    tool_key: str
    exception_type: str
    audit_seq: int


# ---------------------------------------------------------------------------
# Resolved constraints — read from constitution.yaml at dispatch time.
# Mirrors the constitution.tools entry shape so callers don't import
# the constitution dataclass.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _ResolvedToolConstraints:
    name: str
    version: str
    side_effects: str
    constraints: dict[str, Any] = field(default_factory=dict)
    applied_rules: tuple[str, ...] = ()


def _load_initiative_level(constitution_path: Path) -> str:
    """ADR-0021-amendment §2: read the agent's ``initiative_level`` from
    its constitution.yaml.

    Returns ``"L5"`` (back-compat default; no initiative ceiling) when:
      - the file is missing
      - the file lacks an ``agent.initiative_level`` field
      - parsing fails

    Symmetric in shape with :func:`_load_resolved_constraints` —
    pure function, no side effects, defensive against any read failure.
    Called per-dispatch by InitiativeFloorStep. Reading the YAML twice
    per dispatch (once for constraints, once for initiative) is
    acceptable at v0.2; v0.3 may cache.
    """
    if not constitution_path.exists():
        return "L5"
    try:
        data = yaml.safe_load(constitution_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return "L5"
    agent_block = data.get("agent") or {}
    level = agent_block.get("initiative_level")
    if not isinstance(level, str) or not level.strip():
        return "L5"
    return level.strip()


def _load_constitution_mcp_allowlist(constitution_path: Path) -> tuple[str, ...]:
    """ADR-0043 follow-up #2 (Burst 113): read top-level
    ``allowed_mcp_servers`` from the agent's constitution.yaml.

    Returns an empty tuple when:
      - constitution file is missing
      - YAML parsing fails
      - the field is absent (most current constitutions — feature is new)
      - the field is present but not a list

    The dispatcher unions this with active grants from the
    ``agent_plugin_grants`` table to produce the effective allowlist
    that mcp_call.v1 sees. Pure function, defensive against any read
    failure — same posture as :func:`_load_initiative_level`.
    """
    if not constitution_path.exists():
        return ()
    try:
        data = yaml.safe_load(constitution_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ()
    raw = data.get("allowed_mcp_servers")
    if not isinstance(raw, list):
        return ()
    return tuple(s for s in raw if isinstance(s, str) and s)


def _load_resolved_constraints(
    constitution_path: Path, tool_name: str, tool_version: str
) -> _ResolvedToolConstraints | None:
    """Pull the constitution.yaml ``tools:`` entry for this tool, or None.

    Returns None if the file is missing OR the tool is absent from the
    constitution. Callers translate "None" into a refusal — an agent
    cannot dispatch a tool that isn't in its rulebook (different from
    "the tool exists but we say no" which is a constraint outcome).
    """
    if not constitution_path.exists():
        return None
    try:
        data = yaml.safe_load(constitution_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    for entry in data.get("tools") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == tool_name and str(entry.get("version")) == tool_version:
            return _ResolvedToolConstraints(
                name=tool_name,
                version=tool_version,
                side_effects=str(entry.get("side_effects") or ""),
                constraints=dict(entry.get("constraints") or {}),
                applied_rules=tuple(entry.get("applied_rules") or ()),
            )
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tool_key(name: str, version: str) -> str:
    return f"{name}.v{version}"


@dataclass
class ToolDispatcher:
    """Fast-path dispatch coordinator.

    Constructor takes the four sub-systems it touches; tests inject
    fakes for any of them. The daemon stashes a single instance on
    ``app.state.tool_dispatcher`` (T2d) and reuses it per-request.
    """

    registry: ToolRegistry
    audit: AuditChain
    counter_get: Any  # callable: (instance_id, session_id) -> int
    counter_inc: Any  # callable: (instance_id, session_id, when_iso) -> int
    # ADR-0019 T4: per-call accounting writer. Takes the same fields
    # as Registry.record_tool_call. Optional — when None, the dispatcher
    # skips the registry mirror and only emits to the audit chain.
    # Tests use ``None`` to keep the in-memory fakes simple.
    record_call: Any = None  # callable (kwargs) -> None | None
    # ADR-0019 T3: approval-queue persistence. Takes the same fields
    # as Registry.record_pending_approval. When None, the dispatcher
    # mints the ticket_id but does not persist — the approval queue
    # endpoints will see no rows. Tests use ``None`` for in-memory
    # flows; the daemon wires the real registry method.
    pending_writer: Any = None  # callable (kwargs) -> None | None
    # ADR-0019 T6: genre runtime enforcement. The engine's
    # role_to_genre + genres.{name}.risk_profile is consulted at
    # dispatch time:
    #   - if tool.side_effects exceeds genre.max_side_effects → refuse
    #   - if genre.provider_constraint == "local_only" and the active
    #     provider isn't local → refuse
    # When None, no enforcement. Roles unclaimed by any genre also
    # pass through. The check is symmetric with ADR-0021 T5's
    # build-time kit-tier enforcement — T5 catches the static birth
    # case, T6 catches mid-session changes (e.g. tools_add bypass).
    genre_engine: Any = None  # forest_soul_forge.core.genre_engine.GenreEngine | None
    # ADR-0022 v0.1: bound Memory instance. Set on every ToolContext
    # so memory-aware tools (memory_recall.v1, future memory_write.v1)
    # can read/write without re-opening the registry connection.
    # Same instance shared across dispatches — single-writer SQLite
    # discipline preserved by the daemon's write lock.
    memory: Any = None
    # ADR-0033 A3: optional factory that the dispatcher invokes once
    # per dispatch to build a per-call delegate callable bound to the
    # caller's identity. Signature: ``factory(caller_instance_id,
    # caller_dna) -> Callable``. The returned callable lands on
    # ``ToolContext.delegate`` so tools (notably ``delegate.v1``)
    # invoke another agent's skill without reaching back into daemon
    # state. None when delegation isn't wired (test contexts that
    # don't exercise cross-agent calls); ``delegate.v1`` refuses
    # cleanly in that case rather than crashing.
    delegator_factory: Any = None
    # ADR-0033 A6: bound PrivClient instance for the privileged-ops
    # tools. Same posture as ``memory`` — set on every ToolContext
    # so isolate_process.v1, dynamic_policy.v1, and tamper_detect.v1
    # don't have to reach back into daemon state. None when the
    # sudo helper isn't installed; the privileged tools refuse
    # cleanly in that case rather than crashing.
    priv_client: Any = None
    # ADR-003X Phase C6: bound agent Registry. Read-only use by tools
    # that need to enumerate or look up agents (suggest_agent.v1).
    # None when the dispatcher wasn't given a registry (test contexts);
    # the tool refuses cleanly with "no agent registry wired."
    agent_registry: Any = None
    # ADR-0043 T4.5 (Burst 107): plugin runtime view. When set, the
    # dispatcher merges PluginRuntime.mcp_servers_view() into
    # ctx.constraints["mcp_registry"] so mcp_call.v1 sees plugin-
    # registered MCP servers alongside the YAML-curated set. None
    # when plugins aren't wired (test contexts); mcp_call falls
    # back to its YAML-only path. Plugins override YAML on name
    # conflict — the manifest is the operator's newer source of truth.
    plugin_runtime: Any = None
    # ADR-0043 follow-up #2 (Burst 113): post-birth plugin grants
    # accessor. When set, the dispatcher unions
    # ``constitution.allowed_mcp_servers`` with active grants from
    # ``self.plugin_grants.active_plugin_names(instance_id)`` and
    # injects the result as ``ctx.constraints["allowed_mcp_servers"]``.
    # mcp_call.v1's existing allowlist check then sees the union
    # without modification. None when grants aren't wired (test
    # contexts) — the helper falls back to the constitution-only
    # value, which itself defaults to an empty set when the agent
    # was born without an allowed_mcp_servers field. Closes the
    # pre-Burst-113 gap where mcp_call.v1 read this constraint but
    # nothing populated it.
    plugin_grants: Any = None
    # ADR-0060 T2 (Burst 220): post-birth catalog-tool grants accessor.
    # Sister of plugin_grants. When set, ConstraintResolutionStep
    # consults this on constitution-check miss; an active grant
    # substitutes catalog-default constraints for the missing
    # constitution entry so the tool can dispatch without re-birth.
    # Identity (constitution_hash + DNA) stays immutable. None when
    # fsf_registry is None (test contexts) — the step falls through
    # to refuse tool_not_in_constitution, identical to pre-B220 path.
    catalog_grants: Any = None
    # ADR-0060 T2 (Burst 220): tool_catalog reference for the grant
    # lookup to read side_effects defaults from. When None, granted
    # tools fall back to side_effects="unknown" — non-fatal but the
    # downstream PostureGateStep can't gate on it. Wired from
    # ``app.state.tool_catalog`` in deps.py.
    tool_catalog: Any = None
    # ADR-0054 T3 (Burst 180): procedural-shortcut substrate. The
    # dispatcher pre-resolves a shortcut match BEFORE the pipeline
    # runs (the pipeline is sync; embed + search are async) and
    # threads it through DispatchContext. ProceduralShortcutStep
    # converts the pre-computed match into a SHORTCUT verdict that
    # the dispatcher branches on to substitute the recorded action
    # without firing the underlying tool. ALL of these knobs default
    # to a no-op posture so a daemon built without procedural memory
    # behaves identically to pre-T3:
    #   - shortcuts_table=None     → resolver short-circuits to None
    #   - enabled_fn() False       → resolver never calls embed
    #   - tools other than llm_think.v1 → resolver short-circuits
    # See ``_resolve_shortcut_match`` for the full eligibility chain
    # and ``_shortcut_substitute`` for the recorded-action playback.
    procedural_shortcuts_table: Any = None
    # Closures returning the live env-var read so an operator who flips
    # FSF_PROCEDURAL_SHORTCUT_ENABLED at runtime sees the change without
    # restarting the daemon. Default closures keep the substrate OFF
    # for backward compat — operator must opt in (per ADR-0054).
    procedural_shortcut_enabled_fn: Any = None  # callable() -> bool
    procedural_cosine_floor_fn: Any = None      # callable() -> float
    procedural_reinforcement_floor_fn: Any = None  # callable() -> int
    procedural_embed_model_fn: Any = None       # callable() -> str | None

    # ADR-0056 E2 (Burst 188): which role the ModeKitClampStep
    # treats as 'the experimenter'. Default 'experimenter'; tests
    # override to a stub role to validate the no-op-for-other-
    # agents semantic. The step is otherwise a pure-function
    # mode→eligible-tools clamp.
    experimenter_role: str = "experimenter"

    # R3 (2026-04-30): the pipeline of pre-execute checks. Built
    # once per dispatcher in __post_init__ from the dispatcher's
    # injected dependencies. Walked once per dispatch(). Adding a
    # new check (e.g. ADR-003Y per-conversation rate-limit) means
    # appending a step here in the right position; dispatch() doesn't
    # change.
    _pipeline: GovernancePipeline = field(init=False)

    def __post_init__(self) -> None:
        # Lazy import to avoid a circular when genre_engine itself
        # imports from the tools package.
        from forest_soul_forge.core.genre_engine import genre_requires_approval

        self._pipeline = GovernancePipeline(steps=[
            HardwareQuarantineStep(
                audit=self.audit,
                quarantine_reason_fn=_hardware_quarantine_reason,
            ),
            TaskUsageCapStep(
                audit=self.audit,
                session_token_sum_fn=self._sum_session_tokens,
                task_caps_set_fn=self._maybe_emit_task_caps_set,
            ),
            ToolLookupStep(registry=self.registry),
            ArgsValidationStep(),
            ConstraintResolutionStep(
                load_resolved_constraints_fn=_load_resolved_constraints,
                # ADR-0060 T2 (B220): wire the optional grant lookup.
                # Closure captures self.catalog_grants + self.tool_catalog
                # so the step can resolve catalog defaults when a grant
                # fires. None when grants aren't wired (test contexts)
                # — step falls through to pre-B220 refuse path.
                catalog_grant_lookup_fn=self._lookup_catalog_grant,
            ),
            PostureOverrideStep(
                audit=self.audit,
                resolve_active_model_fn=_resolve_active_model_name,
                apply_overrides_fn=_apply_provider_posture_overrides,
            ),
            GenreFloorStep(
                # Closure over self so a test (or the daemon's lifespan
                # hot-reload) reassigning ``dispatcher.genre_engine``
                # is visible immediately to the step. Capturing the
                # engine reference at __post_init__ would freeze the
                # initial value and break that pattern.
                genre_engine_fn=lambda: self.genre_engine,
                check_genre_floor_fn=_check_genre_floor,
            ),
            # ADR-0021-amendment §5 — initiative ladder check, orthogonal
            # to GenreFloorStep's side-effects ceiling. v0.2 enforcement
            # is opt-in per tool: tools that declare a
            # ``required_initiative_level`` get gated; others pass
            # through. Inserts AFTER GenreFloorStep so the side-effects
            # ceiling refusal fires first when both would refuse —
            # operators see the load-bearing ADR-0021 T5 violation
            # rather than the secondary initiative one.
            InitiativeFloorStep(
                initiative_loader_fn=_load_initiative_level,
            ),
            CallCounterStep(counter_get_fn=self.counter_get),
            # Burst 111 (ADR-0043 follow-up): per-tool
            # requires_human_approval mirroring for plugin MCP
            # servers. Sits BEFORE ApprovalGateStep so a per-tool
            # match folds into the constraint-derived gate path
            # (single source of approval truth — see step docstring).
            # No-op when tool is not mcp_call.v1.
            McpPerToolApprovalStep(),
            ApprovalGateStep(
                genre_requires_approval_fn=genre_requires_approval,
            ),
            # ADR-0045 (Bursts 114-115): per-agent posture (traffic
            # light). Outermost gate. Sits AFTER ApprovalGateStep so
            # it can override an upstream GO with REFUSE (red) or
            # PENDING (yellow). Read-only tools always pass through
            # regardless of posture. enforce_per_grant=True (Burst
            # 115 / ADR-0045 T3) layers per-grant trust_tier on top
            # for mcp_call.v1 dispatches: red-dominates precedence
            # across (agent posture, per-grant tier) so a green
            # grant on a yellow agent ungates that specific mcp_call,
            # and a red grant on any agent refuses that mcp_call.
            PostureGateStep(enforce_per_grant=True),
            # ADR-0056 E2 (Burst 188): experimenter mode kit clamp.
            # Sits AFTER PostureGateStep so posture refusals fire
            # first when both would refuse — the operator sees the
            # primary safety mechanism (posture) rather than the
            # secondary kit clamp. No-op for every agent except the
            # experimenter; for that agent reads task_caps.mode and
            # restricts tools per ADR-0056 D2.
            ModeKitClampStep(experimenter_role=self.experimenter_role),
            # ADR-0054 T3 (Burst 180): procedural-shortcut bypass.
            # LAST in the pipeline so all upstream gates (hardware,
            # args, constitution, posture, genre, counter, approval,
            # mode-kit-clamp) have cleared before a shortcut can
            # fire. Returns SHORTCUT terminal verdict only when
            # ``dctx.shortcut_match`` was populated by
            # ``_resolve_shortcut_match`` before the pipeline
            # started; otherwise GO and the dispatch falls through
            # to the underlying tool's execute leg. The step itself
            # is trivial — the heavy lifting (embed + search) is
            # async and lives in the dispatcher to keep the pipeline
            # sync.
            ProceduralShortcutStep(),
        ])

    async def dispatch(
        self,
        *,
        instance_id: str,
        agent_dna: str,
        role: str,
        genre: str | None,
        session_id: str,
        constitution_path: Path,
        tool_name: str,
        tool_version: str,
        args: dict[str, Any],
        provider: Any = None,
        task_caps: dict[str, Any] | None = None,
    ) -> DispatchSucceeded | DispatchRefused | DispatchPendingApproval | DispatchFailed:
        """One round-trip through the runtime. Caller holds the write lock.

        Order is important: validate args BEFORE the counter touches the
        DB so a typo doesn't burn budget. Counter increment happens AFTER
        the approval/refusal decisions but BEFORE execute, so a tool that
        crashes mid-flight still costs the agent a call (otherwise an
        adversarial tool could DoS the budget by always raising).

        R3 (2026-04-30): the 8 pre-execute checks live in
        :class:`GovernancePipeline` (built in ``__post_init__``). The
        body below is the orchestrator: build the call-scoped context,
        run the pipeline, branch on the result, and on GO continue
        into the execute leg.
        """
        # ---- pipeline pre-execute checks --------------------------------
        # Hardware quarantine, task_usage_cap, lookup, validate,
        # constraint resolution, posture overrides, genre floor,
        # counter pre-check, approval gate. See governance_pipeline.py
        # for the per-step rationale.
        #
        # Burst 111 (ADR-0043 follow-up): compute the merged MCP
        # registry view ONCE before the pipeline runs so
        # McpPerToolApprovalStep can consult it. The same merged dict
        # is reused below to populate ctx.constraints["mcp_registry"]
        # for the actual execute leg — single source of truth, no
        # double-merge. None when plugin_runtime is unwired (test
        # contexts) or the merge produced an empty result.
        merged_mcp_registry = self._build_merged_mcp_registry()
        # ADR-0045 T1 (Burst 114): load per-agent posture for the
        # PostureGateStep at the end of the pipeline. None when no
        # agent_registry is wired (test contexts) — step short-
        # circuits to GO. Defensive: any read failure returns None
        # so a corrupt agents row doesn't break dispatch.
        agent_posture = self._load_agent_posture(instance_id)
        # ADR-0045 T3 hook (forward-compat for Burst 115): per-grant
        # trust_tier view. T1 doesn't enforce per-grant, so this is
        # populated but dead. Burst 115 flips PostureGateStep.enforce_per_grant
        # and starts consulting it. Loading here keeps the pipeline
        # interface stable.
        plugin_grants_view = self._load_plugin_grants_view(instance_id)
        # ADR-0054 T3 (Burst 180): pre-resolve any procedural-shortcut
        # match for this dispatch. Async (embed + cosine search) so
        # it runs HERE rather than inside the sync pipeline.
        # Returns ``None`` for any of: substrate unwired, master
        # switch off, ineligible (wrong tool/task_kind/posture/provider),
        # no match above floors, embed/search exception. None means
        # the pipeline's ProceduralShortcutStep returns GO and
        # dispatch proceeds to the normal execute leg.
        shortcut_match = await self._resolve_shortcut_match(
            instance_id=instance_id,
            tool_name=tool_name,
            args=args,
            provider=provider,
            agent_posture=agent_posture,
        )
        # ADR-0056 E2: pull the experimenter mode tag off the
        # operator-supplied task_caps. Defaults to 'none' (no clamp)
        # so non-experimenter dispatches AND experimenter dispatches
        # without a mode tag both behave like every pre-E2 dispatch.
        # ModeKitClampStep is the only consumer.
        mode = "none"
        if task_caps and isinstance(task_caps, dict):
            raw_mode = task_caps.get("mode")
            if isinstance(raw_mode, str) and raw_mode.strip():
                mode = raw_mode.strip().lower()
        dctx = DispatchContext(
            instance_id=instance_id,
            agent_dna=agent_dna,
            role=role,
            genre=genre,
            session_id=session_id,
            constitution_path=constitution_path,
            tool_name=tool_name,
            tool_version=tool_version,
            args=args,
            provider=provider,
            task_caps=task_caps,
            mcp_registry=merged_mcp_registry,
            agent_posture=agent_posture,
            plugin_grants_view=plugin_grants_view,
            shortcut_match=shortcut_match,
            mode=mode,
        )
        verdict = self._pipeline.run(dctx)
        if verdict.is_refuse:
            return self._refuse(
                dctx.key,
                verdict.reason or "unknown",
                verdict.detail or "",
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )
        if verdict.is_pending:
            return self._pending_approval(
                dctx.key,
                tool_name=tool_name, tool_version=tool_version,
                args=args,
                side_effects=verdict.side_effects or "",
                instance_id=instance_id, agent_dna=agent_dna,
                gate_source=verdict.gate_source or "",
                session_id=session_id,
            )
        if verdict.is_shortcut:
            # ADR-0054 T3 (Burst 180). All upstream gates cleared GO
            # AND a high-confidence procedural shortcut matched the
            # situation. Substitute the recorded action_payload
            # without firing the underlying tool. T4 will graduate
            # the audit emission to a dedicated tool_call_shortcut
            # event type; T3 reuses the standard dispatched +
            # succeeded pair with shortcut_applied=True metadata so
            # an operator inspecting the chain can already filter on
            # shortcut hits.
            return await self._shortcut_substitute(
                dctx,
                candidate=verdict.shortcut_candidate,
                similarity=verdict.shortcut_similarity or 0.0,
            )

        # ---- GO — execute leg uses dctx-accumulated state ---------------
        tool = dctx.tool
        resolved = dctx.resolved
        key = dctx.key
        max_calls = int(resolved.constraints.get("max_calls_per_session", 0) or 0)

        # ---- dispatched event + counter increment -----------------------
        # Order: emit `dispatched` BEFORE execute so a crash mid-execute
        # leaves a structured signal (the absence of `succeeded` /
        # `failed` after a `dispatched` is itself diagnostic). Counter
        # increments alongside — a crashing tool still costs a slot to
        # prevent DoS by always-raising.
        when = _now_iso()
        post_count = int(self.counter_inc(instance_id, session_id, when))
        dispatched_entry = self.audit.append(
            EVENT_DISPATCHED,
            {
                "tool_key": key,
                "instance_id": instance_id,
                "session_id": session_id,
                "args_digest": _digest(args),
                "call_count": post_count,
                "max_calls_per_session": max_calls,
                "side_effects": resolved.side_effects or tool.side_effects,
                "applied_rules": list(resolved.applied_rules),
            },
            agent_dna=agent_dna,
        )

        # ---- 7. execute -------------------------------------------------
        # ADR-0033 A3: per-dispatch delegate baked with caller identity.
        # Built once per call so the delegate.v1 tool gets the correct
        # caller_instance_id baked in even across concurrent dispatches.
        delegate_fn = (
            self.delegator_factory(instance_id, agent_dna)
            if self.delegator_factory else None
        )
        # T2.2b — fold operator-supplied context_cap_tokens into the
        # constraints dict so LLM-wrapping tools can read it via
        # ctx.constraints. usage_cap is enforced at the dispatcher
        # level (above), but context_cap is per-LLM-call so tools
        # check it themselves.
        ctx_constraints = dict(resolved.constraints)
        if task_caps:
            ccap = task_caps.get("context_cap_tokens")
            if ccap and isinstance(ccap, int) and ccap > 0:
                ctx_constraints["context_cap_tokens"] = ccap

        # ADR-0043 T4.5: inject plugin-registered MCP servers into
        # ctx.constraints["mcp_registry"] for the execute leg. The
        # merged view was already computed before the pipeline ran
        # (see Burst 111 above) so McpPerToolApprovalStep and
        # mcp_call.v1's runtime see the same registry — single source
        # of truth. Reuse it here rather than re-merging.
        if merged_mcp_registry:
            ctx_constraints["mcp_registry"] = merged_mcp_registry

        # ADR-0043 follow-up #2 (Burst 113): effective allowed_mcp_servers.
        # Union of (constitution top-level allowed_mcp_servers) ∪
        # (active grants from agent_plugin_grants). mcp_call.v1's
        # existing allowlist check reads
        # ``ctx.constraints["allowed_mcp_servers"]``. Pre-Burst-113
        # nothing populated this key — the constitution-side allowlist
        # was documented but never wired into dispatch. Burst 113
        # closes that gap AND adds post-birth augmentation via grants.
        constitution_servers = _load_constitution_mcp_allowlist(constitution_path)
        granted_servers: set[str] = set()
        if self.plugin_grants is not None:
            try:
                granted_servers = self.plugin_grants.active_plugin_names(
                    instance_id,
                )
            except Exception:
                # Grants table issues must not break dispatch — fall
                # back to constitution-only.
                granted_servers = set()
        effective = set(constitution_servers) | granted_servers
        if effective:
            ctx_constraints["allowed_mcp_servers"] = tuple(sorted(effective))
        ctx = ToolContext(
            instance_id=instance_id,
            agent_dna=agent_dna,
            role=role,
            genre=genre,
            session_id=session_id,
            constraints=ctx_constraints,
            provider=provider,
            memory=self.memory,
            delegate=delegate_fn,
            priv_client=self.priv_client,
            agent_registry=self.agent_registry,
            procedural_shortcuts=self.procedural_shortcuts_table,
        )
        try:
            result = await tool.execute(args, ctx)
        except ToolError as e:
            failed_entry = self.audit.append(
                EVENT_FAILED,
                {
                    "tool_key": key,
                    "instance_id": instance_id,
                    "session_id": session_id,
                    "dispatched_seq": dispatched_entry.seq,
                    "exception_type": type(e).__name__,
                    "exception_message": str(e),
                },
                agent_dna=agent_dna,
            )
            self._record_call_safe(
                audit_seq=failed_entry.seq,
                instance_id=instance_id,
                session_id=session_id,
                tool_key=key,
                status="failed",
                tokens_used=None,
                cost_usd=None,
                side_effect_summary=None,
                finished_at=failed_entry.timestamp,
            )
            return DispatchFailed(
                tool_key=key,
                exception_type=type(e).__name__,
                audit_seq=failed_entry.seq,
            )
        except Exception as e:
            # Unexpected exception (the tool didn't subclass ToolError).
            # Surface as a `failed` event but attribute as
            # ``unexpected_exception`` in the metadata so operators can
            # tell wrapped errors from tool-author errors.
            failed_entry = self.audit.append(
                EVENT_FAILED,
                {
                    "tool_key": key,
                    "instance_id": instance_id,
                    "session_id": session_id,
                    "dispatched_seq": dispatched_entry.seq,
                    "exception_type": type(e).__name__,
                    "exception_message": str(e),
                    "unexpected": True,
                },
                agent_dna=agent_dna,
            )
            self._record_call_safe(
                audit_seq=failed_entry.seq,
                instance_id=instance_id,
                session_id=session_id,
                tool_key=key,
                status="failed",
                tokens_used=None,
                cost_usd=None,
                side_effect_summary=None,
                finished_at=failed_entry.timestamp,
            )
            return DispatchFailed(
                tool_key=key,
                exception_type=type(e).__name__,
                audit_seq=failed_entry.seq,
            )

        # ---- 8. succeeded event ----------------------------------------
        succeeded_entry = self.audit.append(
            EVENT_SUCCEEDED,
            {
                "tool_key": key,
                "instance_id": instance_id,
                "session_id": session_id,
                "dispatched_seq": dispatched_entry.seq,
                "result_digest": result.result_digest(),
                "tokens_used": result.tokens_used,
                "cost_usd": result.cost_usd,
                "side_effect_summary": result.side_effect_summary,
            },
            agent_dna=agent_dna,
        )
        # T4: mirror into registry tool_calls for queryable roll-ups.
        # Same write-lock window as the audit append above so a crash
        # leaves the chain and the table mutually consistent.
        self._record_call_safe(
            audit_seq=succeeded_entry.seq,
            instance_id=instance_id,
            session_id=session_id,
            tool_key=key,
            status="succeeded",
            tokens_used=result.tokens_used,
            cost_usd=result.cost_usd,
            side_effect_summary=result.side_effect_summary,
            finished_at=succeeded_entry.timestamp,
        )
        return DispatchSucceeded(
            tool_key=key,
            result=result,
            call_count_after=post_count,
            audit_seq=succeeded_entry.seq,
        )

    # ---- helpers -----------------------------------------------------
    def _record_call_safe(self, **kwargs) -> None:
        """Best-effort write to tool_calls. ``record_call=None`` skips
        the mirror entirely (used by tests with in-memory fakes).
        Any exception is swallowed and would surface in the next chain
        verification — the audit chain remains the source of truth.
        """
        if self.record_call is None:
            return
        try:
            self.record_call(**kwargs)
        except Exception:
            # Defensive — a registry write failure shouldn't break the
            # dispatch outcome the caller already observed in the chain.
            # Verify-on-boot will surface the mismatch.
            pass

    def _refuse(
        self,
        key: str,
        reason: str,
        detail: str,
        *,
        instance_id: str,
        agent_dna: str,
        session_id: str,
    ) -> DispatchRefused:
        entry = self.audit.append(
            EVENT_REFUSED,
            {
                "tool_key": key,
                "instance_id": instance_id,
                "session_id": session_id,
                "reason": reason,
                "detail": detail,
            },
            agent_dna=agent_dna,
        )
        return DispatchRefused(
            tool_key=key, reason=reason, detail=detail,
            audit_seq=entry.seq,
        )

    # ----- T2.2b helpers ---------------------------------------------------
    def _maybe_emit_task_caps_set(
        self, task_caps: dict, *,
        instance_id: str, agent_dna: str, session_id: str, key: str,
    ) -> None:
        """Emit task_caps_set audit event the first time we see operator
        caps for a (session, tool) pair. Idempotent — uses session-level
        de-dup so a session with N dispatches under the same caps emits
        ONE event, not N. The de-dup is best-effort (per-process memory)
        — a daemon restart re-emits, which is acceptable."""
        # Track per-process to avoid spamming. The key includes session_id
        # so two operators each batching a session don't cross-eject one
        # another's de-dup state.
        if not hasattr(self, "_task_caps_emitted"):
            self._task_caps_emitted: set[str] = set()
        dedup_key = f"{session_id}|{task_caps.get('context_cap_tokens')}|{task_caps.get('usage_cap_tokens')}"
        if dedup_key in self._task_caps_emitted:
            return
        self._task_caps_emitted.add(dedup_key)
        try:
            self.audit.append(
                "task_caps_set",
                {
                    "instance_id":          instance_id,
                    "session_id":           session_id,
                    "tool_key":             key,
                    "context_cap_tokens":   task_caps.get("context_cap_tokens"),
                    "usage_cap_tokens":     task_caps.get("usage_cap_tokens"),
                },
                agent_dna=agent_dna,
            )
        except Exception:
            # Audit failure shouldn't mask the dispatch.
            pass

    def _load_agent_posture(self, instance_id: str) -> str | None:
        """Read agents.posture for the given agent. ADR-0045 T1.

        Returns ``None`` when:
          - self.agent_registry is unwired (test contexts)
          - the agent doesn't exist in the registry
          - the row exists but lacks a posture column (shouldn't
            happen post-v15 but defensive)
          - any DB read fails

        On any None return, PostureGateStep short-circuits to GO —
        posture enforcement requires positive evidence.
        """
        if self.agent_registry is None:
            return None
        try:
            conn = getattr(self.agent_registry, "_conn", None)
            if conn is None:
                return None
            row = conn.execute(
                "SELECT posture FROM agents WHERE instance_id = ?;",
                (instance_id,),
            ).fetchone()
            if row is None:
                return None
            posture = row[0]
            if posture not in ("green", "yellow", "red"):
                return None
            return posture
        except Exception:
            return None

    async def _resolve_shortcut_match(
        self,
        *,
        instance_id: str,
        tool_name: str,
        args: dict[str, Any],
        provider: Any,
        agent_posture: str | None,
    ) -> tuple[Any, float] | None:
        """ADR-0054 T3 (Burst 180) — pre-pipeline procedural-shortcut
        resolver.

        Walks the eligibility chain and, when all gates pass, awaits
        the embed + cosine search to produce a candidate match.
        Returns the (ProceduralShortcut, cosine) tuple on a high-
        confidence hit; ``None`` for every other outcome.

        Eligibility (ALL must hold; first failure short-circuits):

          1. ``self.procedural_shortcuts_table`` is wired (None in
             tests/legacy daemons).
          2. ``self.procedural_shortcut_enabled_fn`` exists AND
             returns truthy. The fn typically reads
             ``FSF_PROCEDURAL_SHORTCUT_ENABLED`` so an operator can
             flip the master switch at runtime without rebuild.
          3. Tool is ``llm_think`` (the only conversational dispatch
             path; shortcuts are about bypassing LLM round-trips).
          4. ``args.task_kind`` is "conversation" or unset (defaults
             to "conversation"). classify/safety_check/generate
             shouldn't shortcut — those task_kinds are typically
             pipeline-internal and need fresh model output.
          5. ``args.prompt`` is a non-empty string.
          6. ``provider`` exists AND has an ``embed`` method.
             Frontier providers without embed() fall through.
          7. ``agent_posture`` is None (test contexts) or "green".
             Yellow/red postures bypass shortcuts so operator-
             installed monitoring/refusal triggers fire normally
             on every conversation turn.

        Resolution failures (after eligibility passed):

          * ``EmbeddingError`` from ``embed_situation`` — Ollama
            offline, malformed response, all-zero vector, etc. Logs
            nothing (tests would be noisy) and returns None. The
            dispatch falls through to ``llm_think`` which surfaces
            the underlying provider failure cleanly. The shortcut
            substrate degrades silently to no-shortcut.
          * Search returns empty — no row above the cosine +
            reinforcement floors.
          * Any unexpected exception — defensive None return; never
            crash dispatch on a shortcut-substrate bug.

        The cosine_floor + reinforcement_floor are read from
        injected closures so an operator's runtime knob change is
        seen immediately. Defaults track ADR-0054 D2: 0.92 cosine,
        2 net reinforcement.
        """
        # 1. Substrate wired?
        table = self.procedural_shortcuts_table
        if table is None:
            return None
        # 2. Master switch on?
        enabled_fn = self.procedural_shortcut_enabled_fn
        if enabled_fn is None or not enabled_fn():
            return None
        # 3. Right tool?
        if tool_name != "llm_think":
            return None
        # 4. Right task_kind? (default "conversation" matches
        #    llm_think.DEFAULT_TASK_KIND so an unset arg is treated
        #    as conversation, consistent with the tool's own logic.)
        task_kind = args.get("task_kind", "conversation")
        if task_kind != "conversation":
            return None
        # 5. Non-empty prompt?
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        # 6. Provider has embed()?
        if provider is None or not hasattr(provider, "embed"):
            return None
        # 7. Posture eligible?
        if agent_posture is not None and agent_posture != "green":
            return None

        # All gates passed. Try to resolve a match.
        try:
            from forest_soul_forge.core.memory.procedural_embedding import (
                EmbeddingError,
                embed_situation,
            )
            embed_model = (
                self.procedural_embed_model_fn()
                if self.procedural_embed_model_fn else None
            )
            query_vec = await embed_situation(
                provider, prompt, model=embed_model,
            )
        except EmbeddingError:
            return None
        except Exception:
            # Defensive — never crash dispatch on shortcut bugs.
            return None

        try:
            cosine_floor = (
                float(self.procedural_cosine_floor_fn())
                if self.procedural_cosine_floor_fn else 0.92
            )
            reinforcement_floor = (
                int(self.procedural_reinforcement_floor_fn())
                if self.procedural_reinforcement_floor_fn else 2
            )
            matches = table.search_by_cosine(
                instance_id,
                query_vec,
                cosine_floor=cosine_floor,
                reinforcement_floor=reinforcement_floor,
                top_k=1,
            )
        except Exception:
            return None
        if not matches:
            return None
        # search_by_cosine returns list[tuple[ProceduralShortcut, float]]
        return matches[0]

    async def _shortcut_substitute(
        self,
        dctx: DispatchContext,
        *,
        candidate: Any,
        similarity: float,
    ) -> "DispatchSucceeded":
        """ADR-0054 T3 (Burst 180) + T4 (Burst 181) — substitute the
        recorded action and emit a single ``tool_call_shortcut``
        audit event.

        Called when ``ProceduralShortcutStep`` returns SHORTCUT:

          1. Counter increment (a shortcut still costs a slot —
             otherwise an adversarial pattern could bypass
             max_calls_per_session by always matching).
          2. Build a synthetic ToolResult from the recorded
             ``action_payload``. For action_kind="response" the
             payload is ``{"response": "...", ...}`` matching the
             llm_think.v1 output shape so callers don't notice a
             difference.
          3. **T4 graduation:** emit ONE
             ``tool_call_shortcut`` event with the full picture —
             tool_key, args_digest, result_digest, shortcut_id,
             shortcut_similarity, shortcut_action_kind,
             tokens_used, call_count, side_effects, applied_rules.
             Replaces the dispatched + succeeded pair that T3
             temporarily emitted with shortcut_applied=True
             metadata. Why a dedicated type: a shortcut isn't a
             tool execution — the underlying tool never ran — so
             a distinct event keeps the substitution explicitly
             visible rather than buried in metadata. Operators
             querying "what did this agent do?" need to OR
             tool_call_succeeded + tool_call_shortcut for the
             complete picture; that asymmetry IS the legibility
             we want.
          4. ``record_match()`` updates last_matched_at +
             last_matched_seq on the row so reinforcement
             telemetry (T5) can see this was used.

        Action-kind handling: T3/T4 substitute for ``"response"``.
        ``"tool_call"`` and ``"no_op"`` emit a ``tool_call_failed``
        with ``ShortcutUnsupportedKind`` so the operator can find +
        fix the row.
        """
        action_kind = getattr(candidate, "action_kind", None)
        action_payload = getattr(candidate, "action_payload", None) or {}
        shortcut_id = getattr(candidate, "shortcut_id", None)

        when = _now_iso()
        post_count = int(self.counter_inc(
            dctx.instance_id, dctx.session_id, when,
        ))

        if action_kind != "response":
            # Out-of-scope action_kind. The failed event still
            # carries the shortcut metadata so an operator
            # filtering on shortcut_id can locate the row that
            # tripped this (the failed event is NOT a
            # tool_call_shortcut — it's a regular tool_call_failed
            # because the substitution itself failed).
            failed_entry = self.audit.append(
                EVENT_FAILED,
                {
                    "tool_key": dctx.key,
                    "instance_id": dctx.instance_id,
                    "session_id": dctx.session_id,
                    "exception_type": "ShortcutUnsupportedKind",
                    "exception_message": (
                        f"shortcut_id={shortcut_id} has action_kind="
                        f"{action_kind!r} which is not yet supported "
                        f"by the dispatcher (response only)"
                    ),
                    "shortcut_id": shortcut_id,
                    "shortcut_similarity": round(float(similarity), 6),
                    "shortcut_action_kind": action_kind,
                },
                agent_dna=dctx.agent_dna,
            )
            self._record_call_safe(
                audit_seq=failed_entry.seq,
                instance_id=dctx.instance_id,
                session_id=dctx.session_id,
                tool_key=dctx.key,
                status="failed",
                tokens_used=None,
                cost_usd=None,
                side_effect_summary=None,
                finished_at=failed_entry.timestamp,
            )
            return DispatchFailed(
                tool_key=dctx.key,
                exception_type="ShortcutUnsupportedKind",
                audit_seq=failed_entry.seq,
            )

        # Build the synthetic ToolResult first — its result_digest
        # lands in the audit event below.
        side_effects = (
            (dctx.resolved.side_effects if dctx.resolved else None)
            or (dctx.tool.side_effects if dctx.tool else "read_only")
        )
        applied_rules = (
            list(dctx.resolved.applied_rules) if dctx.resolved else []
        )

        response_text = ""
        if isinstance(action_payload, dict):
            response_text = str(action_payload.get("response") or "")
        synthetic_output = {
            "response":   response_text,
            "model":      "shortcut",
            "task_kind":  dctx.args.get("task_kind", "conversation"),
            "elapsed_ms": 0,
        }
        # Preserve any extra fields the recorded payload carried
        # (e.g., model identifier from the original dispatch) so
        # downstream consumers see them, but never let them clobber
        # the four canonical keys above.
        if isinstance(action_payload, dict):
            for k, v in action_payload.items():
                if k not in synthetic_output:
                    synthetic_output[k] = v

        result = ToolResult(
            output=synthetic_output,
            metadata={
                "prompt_chars":    len(dctx.args.get("prompt", "")),
                "response_chars":  len(response_text),
                "shortcut_id":     shortcut_id,
                "shortcut_similarity": round(float(similarity), 6),
            },
            tokens_used=0,  # zero — the LLM never ran
            cost_usd=None,
            side_effect_summary=(
                f"shortcut: id={shortcut_id} cosine="
                f"{similarity:.4f} (no LLM round-trip)"
            ),
        )

        # T4 — single dedicated event. Carries everything an
        # operator needs to reconstruct the substitution: input
        # digest, recorded output digest, the shortcut row that
        # matched, and counter accounting. No
        # tool_call_dispatched + tool_call_succeeded pair anymore.
        shortcut_entry = self.audit.append(
            EVENT_SHORTCUT,
            {
                "tool_key":             dctx.key,
                "instance_id":          dctx.instance_id,
                "session_id":           dctx.session_id,
                "args_digest":          _digest(dctx.args),
                "result_digest":        result.result_digest(),
                "shortcut_id":          shortcut_id,
                "shortcut_similarity":  round(float(similarity), 6),
                "shortcut_action_kind": action_kind,
                "tokens_used":          result.tokens_used,
                "call_count":           post_count,
                "side_effects":         side_effects,
                "applied_rules":        applied_rules,
                "side_effect_summary":  result.side_effect_summary,
            },
            agent_dna=dctx.agent_dna,
        )
        # Mirror to the registry tool_calls table so per-session
        # roll-ups still account for this dispatch (status="shortcut"
        # so the operator can filter shortcut hits explicitly without
        # parsing audit events).
        self._record_call_safe(
            audit_seq=shortcut_entry.seq,
            instance_id=dctx.instance_id,
            session_id=dctx.session_id,
            tool_key=dctx.key,
            status="shortcut",
            tokens_used=result.tokens_used,
            cost_usd=result.cost_usd,
            side_effect_summary=result.side_effect_summary,
            finished_at=shortcut_entry.timestamp,
        )

        # Update last_matched_at + last_matched_seq on the row so
        # reinforcement tools (T5) and the chat-tab thumbs surface
        # see this match. Best-effort — a write failure here
        # doesn't invalidate the dispatch the operator already saw
        # succeed via the chain entry.
        try:
            if shortcut_id and self.procedural_shortcuts_table is not None:
                self.procedural_shortcuts_table.record_match(
                    shortcut_id, at_seq=shortcut_entry.seq,
                )
        except Exception:
            pass

        return DispatchSucceeded(
            tool_key=dctx.key,
            result=result,
            call_count_after=post_count,
            audit_seq=shortcut_entry.seq,
        )

    def _load_plugin_grants_view(
        self, instance_id: str,
    ) -> dict[str, str] | None:
        """Read active plugin grants for the agent as a
        plugin_name → trust_tier dict. ADR-0045 T3 forward-compat.

        Returns ``None`` when self.plugin_grants is unwired or any
        read fails. Empty dict (no active grants) returns as ``{}``,
        which is distinct from None — the step then knows the table
        IS wired but the agent has no grants, so per-grant tier is
        irrelevant for any tool call.
        """
        if self.plugin_grants is None:
            return None
        try:
            grants = self.plugin_grants.list_active(instance_id)
            return {g.plugin_name: g.trust_tier for g in grants}
        except Exception:
            return None

    def _lookup_catalog_grant(
        self, instance_id: str, tool_name: str, tool_version: str,
    ) -> tuple[_ResolvedToolConstraints, int, str] | None:
        """ADR-0060 T2 / T4: resolve a runtime catalog-tool grant.

        Called by ``ConstraintResolutionStep`` when the constitution
        lookup returns None. Returns
        ``(resolved, granted_at_seq, trust_tier)`` if an active grant
        exists for this (agent, name, version), else None.

        The synthetic ``_ResolvedToolConstraints`` carries:
          - ``side_effects`` from the catalog ToolDef (so PostureGateStep
            still works); ``"unknown"`` when the catalog isn't wired
            (test context) — non-fatal but blocks side-effect-aware
            posture gates from tightening.
          - empty ``constraints`` dict — per ADR-0060 D1, grants don't
            inherit constitution overrides; they use catalog defaults
            (max_calls_per_session=1000, requires_human_approval=false,
            audit_every_call=true at the dispatcher level).
          - ``applied_rules=("granted_via:catalog_grant",)`` so an
            auditor reading the chain knows this dispatch came from
            a grant rather than the constitution.

        ``None`` paths:
          - self.catalog_grants unwired (test context)
          - no active grant for the (agent, name, version) triple
          - read failure (defensive — same posture as
            _load_plugin_grants_view)
        """
        if self.catalog_grants is None:
            return None
        try:
            grant = self.catalog_grants.get_active(
                instance_id, tool_name, tool_version,
            )
        except Exception:
            return None
        if grant is None:
            return None
        # Look up side_effects from the catalog when wired.
        side_effects = "unknown"
        if self.tool_catalog is not None:
            try:
                td = self.tool_catalog.tools.get(f"{tool_name}.v{tool_version}")
                if td is not None:
                    side_effects = td.side_effects
            except Exception:
                pass
        resolved = _ResolvedToolConstraints(
            name=tool_name,
            version=tool_version,
            side_effects=side_effects,
            constraints={},
            applied_rules=("granted_via:catalog_grant",),
        )
        return (resolved, grant.granted_at_seq, grant.trust_tier)

    def _build_merged_mcp_registry(self) -> dict[str, Any] | None:
        """Compute the merged MCP registry view (YAML base + plugin
        overrides). Burst 111 (ADR-0043 follow-up).

        Called once per dispatch BEFORE the pipeline runs so
        :class:`McpPerToolApprovalStep` can consult it. The same dict
        is reused to populate ``ctx.constraints["mcp_registry"]`` for
        the execute leg — single source of truth.

        Returns ``None`` when:
          - ``self.plugin_runtime`` is unwired (test contexts), AND
          - the YAML loader returns nothing (no curated registry)

        Otherwise returns the merge: YAML is the base, plugin manifests
        override by name. Operators expressing a server via plugin are
        saying "this is the source of truth"; the YAML is the legacy
        / pre-plugin path.

        Errors swallowed: any failure in plugin_runtime or YAML loader
        falls back to an empty contribution rather than breaking the
        dispatch. mcp_call.v1's own registry loader is the final
        fallback if this returns None.
        """
        plugin_view: dict[str, Any] = {}
        if self.plugin_runtime is not None:
            try:
                plugin_view = self.plugin_runtime.mcp_servers_view() or {}
            except Exception:
                plugin_view = {}

        yaml_view: dict[str, Any] = {}
        try:
            from forest_soul_forge.tools.builtin.mcp_call import (
                _load_registry as _load_yaml_registry,
            )
            yaml_view = _load_yaml_registry() or {}
        except Exception:
            yaml_view = {}

        if not plugin_view and not yaml_view:
            return None
        merged: dict[str, Any] = dict(yaml_view)
        merged.update(plugin_view)  # plugins win on name conflict
        return merged

    def _sum_session_tokens(self, instance_id: str, session_id: str) -> int:
        """Sum tokens_used across prior dispatches in (instance, session).

        Reads from the registry's tool_calls table when available. Falls
        back to 0 when no record_call writer is wired (test contexts).
        Treats missing/None tokens_used values as 0.
        """
        if self.record_call is None:
            return 0
        try:
            # We need a counterpart — sum_tokens_for_session — on the
            # registry. If it exists, use it. Otherwise fall back to 0
            # and rely on the per-call counter for budget enforcement.
            registry = getattr(self.audit, "_registry", None)
            if registry is None:
                # The dispatcher doesn't currently hold the agent
                # registry directly — but we added it in G6 as
                # self.agent_registry. Use that.
                registry = self.agent_registry
            if registry is None:
                return 0
            method = getattr(registry, "sum_session_tokens", None)
            if method is None:
                # Fallback: ask the SQLite directly via raw conn.
                conn = getattr(registry, "_conn", None)
                if conn is None:
                    return 0
                row = conn.execute(
                    "SELECT COALESCE(SUM(tokens_used), 0) AS total "
                    "FROM tool_calls WHERE instance_id = ? AND session_id = ?",
                    (instance_id, session_id),
                ).fetchone()
                return int(row[0] if not hasattr(row, "keys") else row["total"])
            return int(method(instance_id, session_id))
        except Exception:
            return 0

    def _pending_approval(
        self,
        key: str,
        *,
        tool_name: str,
        tool_version: str,
        args: dict[str, Any],
        side_effects: str,
        instance_id: str,
        agent_dna: str,
        session_id: str,
        gate_source: str = "constraint",
    ) -> DispatchPendingApproval:
        """Emit pending_approval event + persist a queue row.

        T2 minted a stub ticket_id with no registry row behind it. T3
        keeps the same id format (operators who already saw a ticket
        from a pre-T3 daemon can still look it up after upgrade) but
        now writes the row so the approval-queue endpoints can list
        and decide it.

        ``gate_source`` (ADR-0033 A4) records which gate fired:
        ``"constraint"`` for the tool's constitution, ``"genre"`` for
        the security-tier auto-elevation, ``"constraint+genre"`` when
        both fired. Persisted in the audit event so an operator
        inspecting a pending ticket can see whether it's a tool-level
        rule or a tier-level policy that's holding it.
        """
        entry = self.audit.append(
            EVENT_PENDING_APPROVAL,
            {
                "tool_key": key,
                "instance_id": instance_id,
                "session_id": session_id,
                "side_effects": side_effects,
                "gate_source": gate_source,
            },
            agent_dna=agent_dna,
        )
        ticket_id = f"pending-{instance_id}-{session_id}-{entry.seq}"

        # T3: persist the queue row. The dispatcher mints the ticket
        # id from the audit seq, so a missing pending_writer (test
        # fakes) still produces a usable ticket — the row just isn't
        # in any registry. The daemon path always wires a real writer.
        if self.pending_writer is not None:
            try:
                self.pending_writer(
                    ticket_id=ticket_id,
                    instance_id=instance_id,
                    session_id=session_id,
                    tool_key=key,
                    args_json=_canonical_json(args),
                    side_effects=side_effects,
                    pending_audit_seq=entry.seq,
                    created_at=entry.timestamp,
                )
            except Exception:
                # Defensive — same posture as record_call. Write
                # failure shouldn't break the dispatch outcome the
                # operator already observed in the chain.
                pass

        return DispatchPendingApproval(
            tool_key=key, ticket_id=ticket_id, side_effects=side_effects,
            audit_seq=entry.seq,
        )

    # -------- T3 resume / reject paths -----------------------------------
    async def resume_approved(
        self,
        *,
        ticket_id: str,
        operator_id: str,
        instance_id: str,
        agent_dna: str,
        role: str,
        genre: str | None,
        session_id: str,
        constitution_path: Path,
        tool_name: str,
        tool_version: str,
        args: dict[str, Any],
        provider: Any = None,
    ) -> DispatchSucceeded | DispatchFailed | DispatchRefused:
        """Replay a previously-gated tool call after operator approval.

        Caller is the approval endpoint — it has already validated the
        ticket exists and is still pending, marked it approved in the
        registry, and emitted the ``tool_call_approved`` audit event.
        This method then runs the tool exactly like a fast-path
        dispatch *minus the approval gate*: counter check + execute +
        audit + accounting.

        Returning DispatchRefused here means the resume itself was
        refused (tool unregistered between queue + approve, args
        re-validated and failed, max_calls hit) — the endpoint surfaces
        that as a 4xx the same way it does for a fresh dispatch.
        """
        key = _tool_key(tool_name, tool_version)

        # Re-lookup the tool. Plugins might have been hot-reloaded
        # since the queue entry was written; re-fail cleanly if the
        # tool no longer exists.
        tool = self.registry.get(tool_name, tool_version)
        if tool is None:
            return self._refuse(
                key, "unknown_tool",
                f"tool {key} no longer registered at resume time",
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )

        # Re-validate args. Constitutions may have changed; if a tool's
        # validate() rejects what was OK at queue time, we refuse.
        try:
            tool.validate(args)
        except (ToolValidationError, ToolError) as e:
            return self._refuse(
                key, "bad_args", str(e),
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )

        # Genre floor still binds even after operator approval. The
        # approval queue is for operator say-so on the agent's
        # constitution (requires_human_approval); the genre floor is a
        # higher-priority policy that applies at the runtime layer.
        # Symmetric with the dispatch() path.
        resolved_for_genre = _load_resolved_constraints(
            constitution_path, tool_name, tool_version
        )
        side_effects_for_genre = (
            (resolved_for_genre.side_effects if resolved_for_genre else "")
            or tool.side_effects
        )
        ok, detail = _check_genre_floor(
            engine=self.genre_engine,
            role=role,
            tool_side_effects=side_effects_for_genre,
            provider=provider,
        )
        if not ok:
            return self._refuse(
                key, "genre_floor_violated", detail or "",
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )

        # Counter pre-check — same rule as fast path. An approval that
        # arrives after the session burned its budget is refused.
        resolved = _load_resolved_constraints(
            constitution_path, tool_name, tool_version
        )
        if resolved is not None:
            max_calls = int(resolved.constraints.get("max_calls_per_session", 0) or 0)
            current = int(self.counter_get(instance_id, session_id))
            if max_calls and current >= max_calls:
                return self._refuse(
                    key, "max_calls_exceeded",
                    f"session {session_id} has {current}/{max_calls} calls "
                    f"used; further dispatches blocked until session reset",
                    instance_id=instance_id, agent_dna=agent_dna,
                    session_id=session_id,
                )

        when = _now_iso()
        post_count = int(self.counter_inc(instance_id, session_id, when))
        dispatched_entry = self.audit.append(
            EVENT_DISPATCHED,
            {
                "tool_key": key,
                "instance_id": instance_id,
                "session_id": session_id,
                "args_digest": _digest(args),
                "call_count": post_count,
                "side_effects": (resolved.side_effects if resolved else tool.side_effects),
                "resumed_from_ticket": ticket_id,
                "approved_by": operator_id,
            },
            agent_dna=agent_dna,
        )

        delegate_fn = (
            self.delegator_factory(instance_id, agent_dna)
            if self.delegator_factory else None
        )
        ctx = ToolContext(
            instance_id=instance_id,
            agent_dna=agent_dna,
            role=role,
            genre=genre,
            session_id=session_id,
            constraints=dict(resolved.constraints) if resolved else {},
            provider=provider,
            memory=self.memory,
            delegate=delegate_fn,
            priv_client=self.priv_client,
            procedural_shortcuts=self.procedural_shortcuts_table,
        )
        try:
            result = await tool.execute(args, ctx)
        except ToolError as e:
            failed_entry = self.audit.append(
                EVENT_FAILED,
                {
                    "tool_key": key,
                    "instance_id": instance_id,
                    "session_id": session_id,
                    "dispatched_seq": dispatched_entry.seq,
                    "exception_type": type(e).__name__,
                    "exception_message": str(e),
                    "resumed_from_ticket": ticket_id,
                },
                agent_dna=agent_dna,
            )
            self._record_call_safe(
                audit_seq=failed_entry.seq,
                instance_id=instance_id, session_id=session_id,
                tool_key=key, status="failed",
                tokens_used=None, cost_usd=None,
                side_effect_summary=None,
                finished_at=failed_entry.timestamp,
            )
            return DispatchFailed(
                tool_key=key, exception_type=type(e).__name__,
                audit_seq=failed_entry.seq,
            )
        except Exception as e:
            failed_entry = self.audit.append(
                EVENT_FAILED,
                {
                    "tool_key": key,
                    "instance_id": instance_id,
                    "session_id": session_id,
                    "dispatched_seq": dispatched_entry.seq,
                    "exception_type": type(e).__name__,
                    "exception_message": str(e),
                    "unexpected": True,
                    "resumed_from_ticket": ticket_id,
                },
                agent_dna=agent_dna,
            )
            self._record_call_safe(
                audit_seq=failed_entry.seq,
                instance_id=instance_id, session_id=session_id,
                tool_key=key, status="failed",
                tokens_used=None, cost_usd=None,
                side_effect_summary=None,
                finished_at=failed_entry.timestamp,
            )
            return DispatchFailed(
                tool_key=key, exception_type=type(e).__name__,
                audit_seq=failed_entry.seq,
            )

        succeeded_entry = self.audit.append(
            EVENT_SUCCEEDED,
            {
                "tool_key": key,
                "instance_id": instance_id,
                "session_id": session_id,
                "dispatched_seq": dispatched_entry.seq,
                "result_digest": result.result_digest(),
                "tokens_used": result.tokens_used,
                "cost_usd": result.cost_usd,
                "side_effect_summary": result.side_effect_summary,
                "resumed_from_ticket": ticket_id,
            },
            agent_dna=agent_dna,
        )
        self._record_call_safe(
            audit_seq=succeeded_entry.seq,
            instance_id=instance_id, session_id=session_id,
            tool_key=key, status="succeeded",
            tokens_used=result.tokens_used,
            cost_usd=result.cost_usd,
            side_effect_summary=result.side_effect_summary,
            finished_at=succeeded_entry.timestamp,
        )
        return DispatchSucceeded(
            tool_key=key, result=result,
            call_count_after=post_count,
            audit_seq=succeeded_entry.seq,
        )

    def emit_approved_event(
        self,
        *,
        ticket_id: str,
        instance_id: str,
        agent_dna: str,
        session_id: str,
        tool_key: str,
        operator_id: str,
    ) -> int:
        """Append a ``tool_call_approved`` entry. Returns the new seq.

        Endpoint calls this BEFORE marking the registry row decided so
        the audit chain has the operator decision linked to the
        original pending-approval entry. Then the endpoint calls
        :meth:`resume_approved` to actually run the tool.
        """
        entry = self.audit.append(
            EVENT_APPROVED,
            {
                "ticket_id": ticket_id,
                "instance_id": instance_id,
                "session_id": session_id,
                "tool_key": tool_key,
                "operator_id": operator_id,
            },
            agent_dna=agent_dna,
        )
        return entry.seq

    def emit_rejected_event(
        self,
        *,
        ticket_id: str,
        instance_id: str,
        agent_dna: str,
        session_id: str,
        tool_key: str,
        operator_id: str,
        reason: str,
    ) -> int:
        """Append a ``tool_call_rejected`` entry. Returns the new seq.

        Symmetrical to :meth:`emit_approved_event`. After this returns,
        the endpoint marks the registry row rejected. The tool never
        runs; the operator's decision is the chain's final word on
        this ticket.
        """
        entry = self.audit.append(
            EVENT_REJECTED,
            {
                "ticket_id": ticket_id,
                "instance_id": instance_id,
                "session_id": session_id,
                "tool_key": tool_key,
                "operator_id": operator_id,
                "reason": reason,
            },
            agent_dna=agent_dna,
        )
        return entry.seq


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _digest(obj: Any) -> str:
    """SHA-256 over canonical JSON. Mirrors ToolResult.result_digest's
    hashing so dispatched/succeeded entries can be cross-referenced
    without holding the args themselves in the audit chain."""
    import hashlib
    import json
    encoded = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# Side-effects ordering — mirrors core.genre_engine._SIDE_EFFECT_TIERS.
# Keep this private to the dispatcher; the comparison is the only place
# that needs the integer ordering. A tool's side_effects is allowed
# only if its rank ≤ the genre's max rank.
_SIDE_EFFECT_RANK = {
    "read_only": 0,
    "network": 1,
    "filesystem": 2,
    "external": 3,
}


def _provider_is_local(provider: Any) -> bool:
    """Best-effort check that the active provider is the local one.

    The genre engine's only provider_constraint today is ``"local_only"``
    (Companion). Without a name attribute we conservatively say
    "not local" so the constraint fires by default when in doubt.
    """
    if provider is None:
        # No provider attached (test path or non-LLM dispatch) — we
        # consider this 'compatible' with local_only because there's
        # no frontier call about to happen anyway. T9 will tighten if
        # this proves too lax.
        return True
    name = getattr(provider, "name", None)
    return isinstance(name, str) and name.strip().lower() == "local"


def _check_genre_floor(
    *,
    engine: Any,
    role: str,
    tool_side_effects: str,
    provider: Any,
) -> tuple[bool, str | None]:
    """Return (ok, detail). ``ok=True`` means the call passes.

    Pure function — no side effects, no audit emission. The caller
    decides whether to refuse (and emit) based on the result.

    Returns ``(True, None)`` when:
      - engine is None (T6 not wired)
      - role isn't claimed by any genre (legacy / unclaimed)
      - genre exists and side_effects + provider both pass

    Returns ``(False, "<axis>: <reason>")`` otherwise.
    """
    if engine is None:
        return True, None
    genre_name = getattr(engine, "role_to_genre", {}).get(role)
    if genre_name is None:
        return True, None
    genre = getattr(engine, "genres", {}).get(genre_name)
    if genre is None:
        return True, None
    rp = genre.risk_profile

    # Side-effects ladder check.
    tool_rank = _SIDE_EFFECT_RANK.get(tool_side_effects, 99)
    max_rank = _SIDE_EFFECT_RANK.get(rp.max_side_effects, 99)
    if tool_rank > max_rank:
        return False, (
            f"side_effects: tool tier {tool_side_effects!r} exceeds "
            f"genre {genre_name!r} max {rp.max_side_effects!r}"
        )

    # Provider constraint check.
    if rp.provider_constraint == "local_only" and not _provider_is_local(provider):
        provider_name = getattr(provider, "name", "unknown")
        return False, (
            f"provider: genre {genre_name!r} requires local_only; "
            f"active provider is {provider_name!r}"
        )

    return True, None


def _canonical_json(obj: Any) -> str:
    """Canonical JSON encoding for ``args_json`` storage. Sort-keys so a
    re-loaded dict round-trips byte-for-byte."""
    import json
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _resolve_active_model_name(provider: Any) -> str | None:
    """Return the model name the provider would use for GENERATE-class tasks.

    GENERATE is the most consequential task kind (it's where reasoning +
    output happen). Other kinds (CLASSIFY, EMBED) are usually small/cheap
    and don't need posture-override scrutiny in v1. If the provider has
    no models map, returns None and the override layer is a no-op.
    """
    if provider is None:
        return None
    try:
        from forest_soul_forge.daemon.providers.base import TaskKind
        models = getattr(provider, "models", None) or {}
        # models may be keyed by TaskKind enum or by string — handle both
        for key, value in models.items():
            if (key == TaskKind.GENERATE) or (str(key) == "TaskKind.GENERATE") or (str(key).lower() == "generate"):
                return str(value) if value else None
    except Exception:
        pass
    return None


def _apply_provider_posture_overrides(
    resolved: _ResolvedToolConstraints,
    constitution_path: Path,
    active_model: str | None,
) -> tuple[_ResolvedToolConstraints, list[str]]:
    """Layer per-model posture overrides on top of resolved constraints.

    T2.2a. Reads the constitution's ``provider_posture_overrides`` block.
    Overrides can ONLY tighten — they may force requires_human_approval
    to True and lower max_calls_per_session, never the reverse. No-op when:

    * constitution has no provider_posture_overrides block
    * active_model is None (no provider, or no GENERATE model configured)
    * active_model isn't a key in the overrides map

    Schema (in constitution YAML, OUTSIDE canonical_body):

      provider_posture_overrides:
        qwen3.6:
          requires_approval_filesystem: true
          requires_approval_external: true
          max_calls_per_session_cap: 30
        gpt120b:
          requires_approval_filesystem: true

    Trait-delta dimensions (caution_delta, suspicion_delta) are NOT
    enforceable per-dispatch in v1 — traits are baked into the
    constitution at birth via tool_policy resolution rules; the
    dispatcher reads constraints, not traits. Trait-delta enforcement
    would require per-dispatch trait re-evaluation; deferred to v2.

    Returns (modified_resolved, list of human-readable tightening notes).
    Empty notes list = no-op.
    """
    if not active_model or not constitution_path.exists():
        return resolved, []
    try:
        import yaml
        data = yaml.safe_load(constitution_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return resolved, []
    block = data.get("provider_posture_overrides") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return resolved, []
    overrides_for_model = block.get(active_model)
    if not isinstance(overrides_for_model, dict):
        return resolved, []

    notes: list[str] = []
    new_constraints = dict(resolved.constraints)

    # max_calls_per_session_cap: only TIGHTENS (caps lower, never raises)
    if "max_calls_per_session_cap" in overrides_for_model:
        try:
            cap = int(overrides_for_model["max_calls_per_session_cap"])
        except (TypeError, ValueError):
            cap = None
        if cap is not None and cap > 0:
            existing = int(new_constraints.get("max_calls_per_session", 1000))
            if cap < existing:
                new_constraints["max_calls_per_session"] = cap
                notes.append(f"max_calls_per_session reduced {existing}→{cap}")

    # requires_approval_filesystem: forces approval ON for filesystem-class tools
    if (
        bool(overrides_for_model.get("requires_approval_filesystem"))
        and resolved.side_effects == "filesystem"
        and not bool(new_constraints.get("requires_human_approval"))
    ):
        new_constraints["requires_human_approval"] = True
        notes.append("requires_human_approval=true forced (filesystem tool)")

    # requires_approval_external: forces approval ON for external-class tools
    if (
        bool(overrides_for_model.get("requires_approval_external"))
        and resolved.side_effects == "external"
        and not bool(new_constraints.get("requires_human_approval"))
    ):
        new_constraints["requires_human_approval"] = True
        notes.append("requires_human_approval=true forced (external tool)")

    if not notes:
        return resolved, []

    # Append a synthetic applied_rules tag so the audit chain shows
    # the override layer fired. Keeps backward-compat with anything
    # filtering on applied_rules.
    applied_rules = list(resolved.applied_rules) + [f"provider_posture:{active_model}"]
    return _ResolvedToolConstraints(
        name=resolved.name,
        version=resolved.version,
        side_effects=resolved.side_effects,
        constraints=new_constraints,
        applied_rules=tuple(applied_rules),
    ), notes


def _hardware_quarantine_reason(constitution_path: Path) -> dict[str, str] | None:
    """Return a quarantine descriptor dict (with 'expected' + 'binding' keys)
    when the agent's constitution is hardware-bound to a different machine,
    else None (no binding OR binding matches this machine).

    Reads the constitution YAML each call. The cost is a single yaml.safe_load
    + a 16-char string compare — well under the noise floor of any tool
    dispatch. We deliberately do NOT cache the result because operator
    /hardware/unbind needs the next dispatch to see the cleared file.

    Returns None on any read/parse failure — a malformed constitution is a
    bigger problem the dispatcher will catch downstream; we don't want
    quarantine to mask the underlying error.
    """
    try:
        import yaml
        text = constitution_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    block = data.get("hardware_binding")
    if not block:
        return None
    if isinstance(block, dict):
        binding = block.get("fingerprint")
    elif isinstance(block, str):
        binding = block
    else:
        return None
    if not isinstance(binding, str) or not binding:
        return None
    try:
        from forest_soul_forge.core.hardware import compute_hardware_fingerprint
        here = compute_hardware_fingerprint().fingerprint
    except Exception:
        # If the fingerprint subsystem itself errored, refuse-by-default
        # would be too aggressive — fall back to no quarantine. The
        # operator will see the underlying error in /healthz instead.
        return None
    if binding == here:
        return None
    return {"expected": here, "binding": binding}
