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
        """
        key = _tool_key(tool_name, tool_version)

        # ---- 0. hardware quarantine (ADR-003X K6) -----------------------
        # Refuse BEFORE tool lookup so a quarantined agent's call doesn't
        # even produce an unknown_tool false-positive when the registry
        # has stale state. Reads the constitution once per dispatch; the
        # check is cheap (yaml load + 16-char string compare).
        quarantine_reason = _hardware_quarantine_reason(constitution_path)
        if quarantine_reason is not None:
            try:
                self.audit.append(
                    "hardware_mismatch",
                    {
                        "instance_id": instance_id,
                        "tool_key": key,
                        "session_id": session_id,
                        "expected_machine_fingerprint": quarantine_reason["expected"],
                        "constitution_binding": quarantine_reason["binding"],
                    },
                    agent_dna=agent_dna,
                )
            except Exception:
                pass
            return self._refuse(
                key, "hardware_quarantined",
                (
                    f"agent {instance_id} is hardware-bound to "
                    f"{quarantine_reason['binding'][:8]}… but this machine is "
                    f"{quarantine_reason['expected'][:8]}…. "
                    "Operator must POST /agents/{id}/hardware/unbind to release."
                ),
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )

        # ---- 0b. T2.2b: per-task usage_cap pre-check ---------------------
        # Operator-supplied task_caps are checked BEFORE tool lookup so
        # the operator's cap takes precedence over any tool that would
        # have been the next dispatch. Sums tokens_used from prior
        # dispatches in the same session — when next-call estimated
        # tokens would push total over usage_cap_tokens, refuse.
        # context_cap_tokens is tool-side enforcement (LLM-wrapping
        # tools check ctx.constraints["context_cap_tokens"]); the
        # dispatcher just passes it through via constraints.
        if task_caps:
            self._maybe_emit_task_caps_set(
                task_caps, instance_id=instance_id,
                agent_dna=agent_dna, session_id=session_id, key=key,
            )
            usage_cap = task_caps.get("usage_cap_tokens")
            if usage_cap and isinstance(usage_cap, int) and usage_cap > 0:
                used = self._sum_session_tokens(instance_id, session_id)
                if used >= usage_cap:
                    return self._refuse(
                        key, "task_usage_cap_exceeded",
                        (
                            f"session {session_id} has consumed {used} tokens; "
                            f"operator-supplied usage_cap_tokens={usage_cap} "
                            "blocks further dispatches. Start a new session "
                            "or raise the cap."
                        ),
                        instance_id=instance_id, agent_dna=agent_dna,
                        session_id=session_id,
                    )

        # ---- 1. lookup ---------------------------------------------------
        tool = self.registry.get(tool_name, tool_version)
        if tool is None:
            return self._refuse(
                key, "unknown_tool",
                f"no tool registered for {key} (registered: {list(self.registry.tools)})",
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )

        # ---- 2. validate args -------------------------------------------
        try:
            tool.validate(args)
        except ToolValidationError as e:
            return self._refuse(
                key, "bad_args", str(e),
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )
        except ToolError as e:
            return self._refuse(
                key, "bad_args", str(e),
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )

        # ---- 3. load resolved constraints from constitution -------------
        resolved = _load_resolved_constraints(constitution_path, tool_name, tool_version)
        if resolved is None:
            if not constitution_path.exists():
                return self._refuse(
                    key, "constitution_missing",
                    f"constitution.yaml not found at {constitution_path}",
                    instance_id=instance_id, agent_dna=agent_dna,
                    session_id=session_id,
                )
            return self._refuse(
                key, "tool_not_in_constitution",
                f"agent's constitution does not list {key} — re-birth or "
                f"add via tools_add to grant access",
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )

        # ---- 3b. apply per-model posture overrides (T2.2a) ---------------
        # Constitution-level provider_posture_overrides let operators
        # codify per-model wisdom (e.g. "qwen3.6 too eager → require
        # approval for filesystem"). Overrides can ONLY tighten — they
        # raise approval gates and lower call caps, never the reverse.
        # No-op when the constitution has no overrides block, OR the
        # active model isn't in the overrides map.
        active_model = _resolve_active_model_name(provider)
        resolved, posture_notes = _apply_provider_posture_overrides(
            resolved, constitution_path, active_model
        )
        if posture_notes:
            try:
                self.audit.append(
                    "posture_override_applied",
                    {
                        "instance_id":   instance_id,
                        "tool_key":      key,
                        "session_id":    session_id,
                        "active_model":  active_model,
                        "tightenings":   posture_notes,
                    },
                    agent_dna=agent_dna,
                )
            except Exception:
                # Audit-emit failure shouldn't mask the actual dispatch.
                pass

        # ---- 4. genre runtime enforcement (ADR-0019 T6) ----------------
        # Symmetric with ADR-0021 T5 (build-time kit-tier check): T5
        # catches what's in the constitution at birth; T6 catches what
        # the runtime is about to invoke. Belt-and-suspenders so a
        # genre tightening or a tools_add slip doesn't let a Companion
        # fire a network tool.
        ok, detail = _check_genre_floor(
            engine=self.genre_engine,
            role=role,
            tool_side_effects=resolved.side_effects or tool.side_effects,
            provider=provider,
        )
        if not ok:
            return self._refuse(
                key, "genre_floor_violated", detail or "",
                instance_id=instance_id, agent_dna=agent_dna,
                session_id=session_id,
            )

        # ---- 5. counter pre-check (read, not yet incremented) ----------
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

        # ---- 6. approval gate -------------------------------------------
        # Two paths can elevate to pending_approval:
        #   (a) the tool's resolved constitution constraint
        #       (existing ADR-0019 T3 behavior)
        #   (b) the agent's genre policy (ADR-0033 A4 graduation):
        #         security_high  → any non-read_only call
        #         security_mid   → filesystem/external
        #         security_low   → no elevation (tool config wins)
        # OR semantics: either path forces approval. Audit metadata
        # records WHICH path fired so an operator inspecting a
        # pending ticket can see "tool config" vs "genre policy".
        constraint_requires = bool(resolved.constraints.get("requires_human_approval", False))
        effective_side_effects = resolved.side_effects or tool.side_effects
        from forest_soul_forge.core.genre_engine import genre_requires_approval
        genre_requires = genre_requires_approval(genre, effective_side_effects)
        if constraint_requires or genre_requires:
            return self._pending_approval(
                key,
                tool_name=tool_name, tool_version=tool_version,
                args=args,
                side_effects=effective_side_effects,
                instance_id=instance_id, agent_dna=agent_dna,
                # Pass through the elevation reason so the ticket
                # row + audit event record which gate fired. The
                # _pending_approval method threads this into the
                # event_data and the ticket payload.
                gate_source=(
                    "constraint+genre" if (constraint_requires and genre_requires)
                    else ("genre" if genre_requires else "constraint")
                ),
                session_id=session_id,
            )

        # ---- 6. dispatched event + counter increment --------------------
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
