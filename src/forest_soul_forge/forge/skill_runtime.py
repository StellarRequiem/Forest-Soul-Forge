"""Skill runtime — ADR-0031 T2.

Walks a :class:`SkillDef` step by step, dispatching each tool call
through the existing :class:`ToolDispatcher`. Emits seven new
audit-chain event types so an inspector can reconstruct the DAG walk
from the chain alone.

The runtime is **decoupled from the dispatcher** — it takes a callable
``dispatch_tool`` that returns a result (or refusal) per tool call.
The daemon wires this to a real ToolDispatcher; tests inject a fake
that returns canned outputs. Same shape as ToolDispatcher's own
counter callbacks.

Skill-level invariants:

* Skills do **not** have their own constraint policy. Every tool
  dispatched inside a skill goes through the agent's normal
  constraints (counter, genre, approval gate). The runtime is a
  thin orchestrator above ToolDispatcher.
* Pending-approval gating during a skill run is **not** the runtime's
  problem in T2. If a tool dispatch returns ``DispatchPendingApproval``,
  the skill terminates with ``outcome=pending_approval`` and the
  caller is responsible for tracking the ticket. Skill-level resume
  (where the operator approves and the skill picks back up where it
  paused) lands in T2.5 once we have demand for it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.forge.skill_expression import (
    ExpressionError,
    Template,
)
from forest_soul_forge.forge.skill_manifest import (
    ForEachStep,
    SkillDef,
    StepNode,
    ToolStep,
)


# ---------------------------------------------------------------------------
# Audit event names — kept module-local for grep'ability. Mirrors the
# tool dispatcher's pattern (EVENT_DISPATCHED etc.).
# ---------------------------------------------------------------------------
EVENT_SKILL_INVOKED = "skill_invoked"
EVENT_SKILL_STEP_STARTED = "skill_step_started"
EVENT_SKILL_STEP_COMPLETED = "skill_step_completed"
EVENT_SKILL_STEP_SKIPPED = "skill_step_skipped"
EVENT_SKILL_STEP_FAILED = "skill_step_failed"
EVENT_SKILL_COMPLETED = "skill_completed"


# ---------------------------------------------------------------------------
# Outcome dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SkillSucceeded:
    """All steps ran (or were intentionally skipped). ``output`` is the
    assembled dict per the manifest's ``output:`` block."""

    skill_name: str
    skill_version: str
    skill_hash: str
    output: dict[str, Any]
    invoked_seq: int
    completed_seq: int
    steps_executed: int
    steps_skipped: int


@dataclass(frozen=True)
class SkillFailed:
    """A step raised an exception (tool dispatch failure, expression
    error, etc.). Earlier successful steps stay in the output_so_far
    binding so an operator can inspect partial state.

    ``failure_reason`` is one of: ``tool_failed`` (DispatchFailed
    propagated), ``tool_refused`` (DispatchRefused propagated),
    ``tool_pending_approval`` (DispatchPendingApproval — skill paused),
    ``expression_error`` (predicate or arg eval failed),
    ``tool_unexpected_outcome`` (dispatcher returned something we
    don't know how to consume).
    """

    skill_name: str
    skill_version: str
    skill_hash: str
    invoked_seq: int
    failed_step_id: str
    failure_reason: str
    detail: str
    bindings_at_failure: dict[str, Any]
    completed_seq: int


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
@dataclass
class SkillRuntime:
    """Stateless walker over SkillDefs.

    ``audit`` owns hash-chain emission. ``dispatch_tool`` is the
    bridge to the tool runtime — taking the tool key + resolved args
    + the agent's role/genre/session and returning whatever the
    dispatcher returns.
    """

    audit: AuditChain
    dispatch_tool: Callable[..., Awaitable[Any]]
    # Optional hook the daemon wires to the registry's audit-event
    # mirror (parallels record_call in ToolDispatcher). When None,
    # only the chain entries land — useful for tests.
    record_event: Any = None

    async def run(
        self,
        *,
        skill: SkillDef,
        instance_id: str,
        agent_dna: str,
        role: str,
        genre: str | None,
        session_id: str,
        inputs: dict[str, Any],
        provider: Any = None,
        dry_run: bool = False,
    ) -> SkillSucceeded | SkillFailed:
        """Execute a skill end-to-end. Caller (the endpoint) must hold
        the daemon write lock — the runtime calls into the audit chain
        repeatedly, and per-step tool dispatches each take their own
        chain entries.

        ``dry_run`` flag is recorded in the skill_invoked event but
        does not currently change behavior (T6 in ADR-0031 will wire
        a real dry-run mode that uses stub providers). T2 just plumbs
        the flag through.
        """
        # ---- 1. emit skill_invoked --------------------------------------
        invoked_entry = self.audit.append(
            EVENT_SKILL_INVOKED,
            {
                "skill_name": skill.name,
                "skill_version": skill.version,
                "skill_hash": skill.skill_hash,
                "instance_id": instance_id,
                "session_id": session_id,
                "inputs_keys": sorted(inputs.keys()),
                "dry_run": dry_run,
            },
            agent_dna=agent_dna,
        )

        # ---- 2. walk steps ---------------------------------------------
        # bindings: the in-flight context. ``inputs`` is bound at
        # entry, each completed step adds a binding under its id.
        bindings: dict[str, Any] = {"inputs": dict(inputs)}
        steps_executed = 0
        steps_skipped = 0
        try:
            steps_executed, steps_skipped = await self._run_steps(
                steps=skill.steps,
                bindings=bindings,
                ambient={"inputs"},
                invoked_seq=invoked_entry.seq,
                instance_id=instance_id,
                agent_dna=agent_dna,
                role=role,
                genre=genre,
                session_id=session_id,
                provider=provider,
            )
        except _SkillStepFailure as fail:
            completed_entry = self.audit.append(
                EVENT_SKILL_COMPLETED,
                {
                    "skill_invoked_seq": invoked_entry.seq,
                    "outcome": "failed",
                    "failed_step_id": fail.step_id,
                    "failure_reason": fail.reason,
                    "executed_steps": steps_executed,
                    "skipped_steps": steps_skipped,
                },
                agent_dna=agent_dna,
            )
            return SkillFailed(
                skill_name=skill.name,
                skill_version=skill.version,
                skill_hash=skill.skill_hash,
                invoked_seq=invoked_entry.seq,
                failed_step_id=fail.step_id,
                failure_reason=fail.reason,
                detail=fail.detail,
                bindings_at_failure=_strip_inputs(bindings),
                completed_seq=completed_entry.seq,
            )

        # ---- 3. assemble output -----------------------------------------
        output = self._assemble_output(skill.output, bindings)

        # ---- 4. emit skill_completed -----------------------------------
        completed_entry = self.audit.append(
            EVENT_SKILL_COMPLETED,
            {
                "skill_invoked_seq": invoked_entry.seq,
                "outcome": "succeeded",
                "executed_steps": steps_executed,
                "skipped_steps": steps_skipped,
                "output_keys": sorted(output.keys()),
            },
            agent_dna=agent_dna,
        )

        return SkillSucceeded(
            skill_name=skill.name,
            skill_version=skill.version,
            skill_hash=skill.skill_hash,
            output=output,
            invoked_seq=invoked_entry.seq,
            completed_seq=completed_entry.seq,
            steps_executed=steps_executed,
            steps_skipped=steps_skipped,
        )

    async def _run_steps(
        self,
        *,
        steps: tuple[StepNode, ...],
        bindings: dict[str, Any],
        ambient: set[str],
        invoked_seq: int,
        instance_id: str,
        agent_dna: str,
        role: str,
        genre: str | None,
        session_id: str,
        provider: Any,
    ) -> tuple[int, int]:
        """Walk a steps list. Returns (executed_count, skipped_count).

        Raises :class:`_SkillStepFailure` on the first failure so the
        outer ``run`` can emit ``skill_completed(outcome=failed)``.
        """
        executed = 0
        skipped = 0
        for step in steps:
            # when / unless predicates — evaluate against current
            # bindings. Either failing to evaluate is a step failure.
            try:
                if step.when is not None and not _to_bool(
                    step.when.evaluate(bindings)
                ):
                    self._emit_skipped(
                        invoked_seq=invoked_seq, step=step,
                        agent_dna=agent_dna,
                        reason="when_predicate_false",
                    )
                    skipped += 1
                    continue
                if step.unless is not None and _to_bool(
                    step.unless.evaluate(bindings)
                ):
                    self._emit_skipped(
                        invoked_seq=invoked_seq, step=step,
                        agent_dna=agent_dna,
                        reason="unless_predicate_true",
                    )
                    skipped += 1
                    continue
            except ExpressionError as e:
                raise _SkillStepFailure(
                    step_id=step.id,
                    reason="expression_error",
                    detail=f"predicate failed: {e}",
                )

            if isinstance(step, ToolStep):
                await self._run_tool_step(
                    step=step, bindings=bindings,
                    invoked_seq=invoked_seq,
                    instance_id=instance_id, agent_dna=agent_dna,
                    role=role, genre=genre, session_id=session_id,
                    provider=provider,
                )
                executed += 1
            elif isinstance(step, ForEachStep):
                inner_executed, inner_skipped = await self._run_for_each(
                    step=step, bindings=bindings, ambient=ambient,
                    invoked_seq=invoked_seq,
                    instance_id=instance_id, agent_dna=agent_dna,
                    role=role, genre=genre, session_id=session_id,
                    provider=provider,
                )
                executed += inner_executed
                skipped += inner_skipped
            else:
                raise _SkillStepFailure(
                    step_id=getattr(step, "id", "?"),
                    reason="tool_unexpected_outcome",
                    detail=f"unknown step type {type(step).__name__}",
                )
        return executed, skipped

    async def _run_tool_step(
        self,
        *,
        step: ToolStep,
        bindings: dict[str, Any],
        invoked_seq: int,
        instance_id: str,
        agent_dna: str,
        role: str,
        genre: str | None,
        session_id: str,
        provider: Any,
    ) -> None:
        """Resolve args, dispatch the tool, bind the result."""
        # Resolve args.
        try:
            resolved_args: dict[str, Any] = {}
            for k, tpl in step.args.items():
                resolved_args[k] = tpl.evaluate(bindings)
        except ExpressionError as e:
            raise _SkillStepFailure(
                step_id=step.id,
                reason="expression_error",
                detail=f"arg resolution failed: {e}",
            )

        # tool_key parse — name.vversion.
        tool_name, _, tool_version = step.tool.rpartition(".v")

        # skill_step_started — emitted BEFORE the dispatch so a crash
        # in the tool runtime leaves a structured "we attempted X"
        # signal even when no terminating event lands.
        started_entry = self.audit.append(
            EVENT_SKILL_STEP_STARTED,
            {
                "skill_invoked_seq": invoked_seq,
                "step_id": step.id,
                "tool_key": step.tool,
                "args_keys": sorted(resolved_args.keys()),
            },
            agent_dna=agent_dna,
        )

        # Dispatch through the tool runtime. The injected callable's
        # contract is: returns one of DispatchSucceeded /
        # DispatchFailed / DispatchRefused / DispatchPendingApproval
        # (mirrors ToolDispatcher.dispatch). Tests inject a fake.
        outcome = await self.dispatch_tool(
            tool_name=tool_name,
            tool_version=tool_version,
            args=resolved_args,
            instance_id=instance_id,
            agent_dna=agent_dna,
            role=role,
            genre=genre,
            session_id=session_id,
            provider=provider,
        )

        # Inspect outcome class by name to avoid hard-imports of
        # dispatcher symbols (tests + decoupling). The dispatcher's
        # outcome dataclasses are stable.
        cls = type(outcome).__name__
        if cls == "DispatchSucceeded":
            # Bind the tool's output dict under the step id. The
            # output IS the binding — `step_a.foo` resolves to
            # `outcome.result.output["foo"]`.
            bindings[step.id] = outcome.result.output
            self.audit.append(
                EVENT_SKILL_STEP_COMPLETED,
                {
                    "skill_invoked_seq": invoked_seq,
                    "step_id": step.id,
                    "tool_call_seq": outcome.audit_seq,
                    "started_seq": started_entry.seq,
                },
                agent_dna=agent_dna,
            )
            return
        if cls == "DispatchFailed":
            self.audit.append(
                EVENT_SKILL_STEP_FAILED,
                {
                    "skill_invoked_seq": invoked_seq,
                    "step_id": step.id,
                    "started_seq": started_entry.seq,
                    "failure_reason": "tool_failed",
                    "tool_call_seq": outcome.audit_seq,
                    "exception_type": getattr(outcome, "exception_type", "?"),
                },
                agent_dna=agent_dna,
            )
            raise _SkillStepFailure(
                step_id=step.id,
                reason="tool_failed",
                detail=f"tool {step.tool} raised "
                       f"{getattr(outcome, 'exception_type', '?')}",
            )
        if cls == "DispatchRefused":
            self.audit.append(
                EVENT_SKILL_STEP_FAILED,
                {
                    "skill_invoked_seq": invoked_seq,
                    "step_id": step.id,
                    "started_seq": started_entry.seq,
                    "failure_reason": "tool_refused",
                    "tool_call_seq": outcome.audit_seq,
                    "refusal_reason": getattr(outcome, "reason", "?"),
                },
                agent_dna=agent_dna,
            )
            raise _SkillStepFailure(
                step_id=step.id,
                reason="tool_refused",
                detail=(
                    f"tool {step.tool} refused: "
                    f"{getattr(outcome, 'reason', '?')} — "
                    f"{getattr(outcome, 'detail', '')}"
                ),
            )
        if cls == "DispatchPendingApproval":
            self.audit.append(
                EVENT_SKILL_STEP_FAILED,
                {
                    "skill_invoked_seq": invoked_seq,
                    "step_id": step.id,
                    "started_seq": started_entry.seq,
                    "failure_reason": "tool_pending_approval",
                    "ticket_id": getattr(outcome, "ticket_id", "?"),
                },
                agent_dna=agent_dna,
            )
            raise _SkillStepFailure(
                step_id=step.id,
                reason="tool_pending_approval",
                detail=(
                    f"tool {step.tool} requires approval (ticket "
                    f"{getattr(outcome, 'ticket_id', '?')}); skill "
                    "paused. Resume via skill rerun after approval — "
                    "skill-level resume lands in T2.5."
                ),
            )
        # Unknown outcome shape. Surface as failure — better than
        # silently binding garbage.
        self.audit.append(
            EVENT_SKILL_STEP_FAILED,
            {
                "skill_invoked_seq": invoked_seq,
                "step_id": step.id,
                "started_seq": started_entry.seq,
                "failure_reason": "tool_unexpected_outcome",
                "outcome_class": cls,
            },
            agent_dna=agent_dna,
        )
        raise _SkillStepFailure(
            step_id=step.id,
            reason="tool_unexpected_outcome",
            detail=f"dispatcher returned unknown outcome type {cls}",
        )

    async def _run_for_each(
        self,
        *,
        step: ForEachStep,
        bindings: dict[str, Any],
        ambient: set[str],
        invoked_seq: int,
        instance_id: str,
        agent_dna: str,
        role: str,
        genre: str | None,
        session_id: str,
        provider: Any,
    ) -> tuple[int, int]:
        """Iterate over an items list, walking inner steps once per
        item. Per-iteration outputs accumulate under
        ``bindings[step.id][inner_step_id]`` as a list — so an outer
        ``${step.inner.foo}`` references collected results."""
        try:
            items = step.items.evaluate(bindings)
        except ExpressionError as e:
            raise _SkillStepFailure(
                step_id=step.id,
                reason="expression_error",
                detail=f"for_each items eval failed: {e}",
            )
        if items is None:
            items = []
        if not hasattr(items, "__iter__") or isinstance(items, (str, bytes, dict)):
            raise _SkillStepFailure(
                step_id=step.id,
                reason="expression_error",
                detail=(
                    f"for_each items evaluated to "
                    f"{type(items).__name__}, expected list-like"
                ),
            )

        # Accumulate inner-step results across iterations under
        # bindings[step.id]. Shape: { inner_id: [iter1_output, ...] }.
        accum: dict[str, list[Any]] = {}
        executed = 0
        skipped = 0
        for item in items:
            inner_bindings = dict(bindings)
            inner_bindings["each"] = item
            iter_executed, iter_skipped = await self._run_steps(
                steps=step.steps,
                bindings=inner_bindings,
                ambient=ambient | {"each"},
                invoked_seq=invoked_seq,
                instance_id=instance_id, agent_dna=agent_dna,
                role=role, genre=genre, session_id=session_id,
                provider=provider,
            )
            executed += iter_executed
            skipped += iter_skipped
            # Collect each inner step's binding back into the accum.
            for inner_step in step.steps:
                if isinstance(inner_step, ToolStep) and inner_step.id in inner_bindings:
                    accum.setdefault(inner_step.id, []).append(
                        inner_bindings[inner_step.id]
                    )
        bindings[step.id] = accum
        return executed, skipped

    def _emit_skipped(
        self,
        *,
        invoked_seq: int,
        step: StepNode,
        agent_dna: str,
        reason: str,
    ) -> None:
        self.audit.append(
            EVENT_SKILL_STEP_SKIPPED,
            {
                "skill_invoked_seq": invoked_seq,
                "step_id": step.id,
                "reason": reason,
            },
            agent_dna=agent_dna,
        )

    def _assemble_output(
        self,
        manifest_output: dict[str, Template],
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate the manifest's output: block against the final
        bindings. Any expression error here is a failure but we
        catch + return what we have so the operator gets partial
        info. Manifest validation (skill_manifest.parse_manifest)
        already verified output references are in scope."""
        out: dict[str, Any] = {}
        for k, tpl in manifest_output.items():
            try:
                out[k] = tpl.evaluate(bindings)
            except ExpressionError as e:
                out[k] = f"<expression_error: {e}>"
        return out


# ---------------------------------------------------------------------------
# Internal failure carrier — short-circuits the step walk
# ---------------------------------------------------------------------------
@dataclass
class _SkillStepFailure(Exception):
    step_id: str
    reason: str
    detail: str

    def __str__(self) -> str:
        return f"step {self.step_id}: {self.reason} — {self.detail}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_bool(v: Any) -> bool:
    """Coerce a predicate value to bool. Mirrors Python's truthiness
    rules but explicit so future changes are clear."""
    return bool(v)


def _strip_inputs(bindings: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of bindings without the ``inputs`` key — when we
    surface bindings_at_failure to the operator, the inputs are
    already in their request payload, no point echoing them back."""
    return {k: v for k, v in bindings.items() if k != "inputs"}
