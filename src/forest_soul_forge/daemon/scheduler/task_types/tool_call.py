"""``tool_call`` task type runner — dispatch one tool call on a schedule.

ADR-0041 T3, Burst 89. Closes the deferred ADR-0036 T4 (Verifier
Loop scheduled scans) by giving the scheduler a runner that invokes
``ToolDispatcher.dispatch`` against an existing agent on the task's
configured cadence.

Config shape (one entry from ``scheduled_tasks.yaml``)::

    - id: verifier_24h_scan
      description: "Verifier scans memory for contradictions daily"
      schedule: "every 24h"
      enabled: true
      type: tool_call
      config:
        agent_id: verifier_loop_001
        tool_name: verifier_scan
        tool_version: "1"
        args:
          lookback_hours: 24
          max_pairs: 100

Required keys: ``agent_id``, ``tool_name``, ``tool_version``.
Optional: ``args`` (default ``{}``), ``task_caps``.

Dispatch path is the same one HTTP callers go through —
``build_or_get_tool_dispatcher(app).dispatch(...)`` — so all
governance applies (constitution, genre kit-tier ceiling,
initiative ladder, per-session counters, approval gates). The
ADR-0041 open-question on approval-queue interaction is resolved
to option (a): if the dispatch lands as ``DispatchPendingApproval``,
the runner returns failure (with ``error="requires_human_approval"``)
so the scheduler doesn't silently drop the call into the queue
without operator visibility. v0.4 doesn't surface "this came from
scheduled task X" in the approval UI yet; option (b) becomes
viable once that's wired.

session_id rotation: the runner uses a stable session_id of
``<task_id>-<YYYYMMDD>`` (UTC date), so a daily scheduled task
gets a fresh session every day — preserves per-day counter
semantics without exhausting a single session's
``max_calls_per_session`` budget over weeks/months. ADR-0041
specifies this as the rate-limit mitigation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_REQUIRED_CONFIG_KEYS = ("agent_id", "tool_name", "tool_version")


async def tool_call_runner(
    config: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch a single tool call against ``config['agent_id']``.

    Pure outcome reporter — never raises. Any error condition
    (missing context, missing agent, malformed config, dispatch
    refusal, approval-pending, dispatch failure, runner exception)
    surfaces as ``{"ok": False, "error": ...}`` so the scheduler's
    circuit-breaker bookkeeping in :meth:`Scheduler._dispatch` can
    do its job uniformly.
    """
    # ---- config validation -------------------------------------------
    missing = [k for k in _REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        return {
            "ok": False,
            "error": f"config missing required keys: {missing}",
        }
    agent_id = str(config["agent_id"])
    tool_name = str(config["tool_name"])
    tool_version = str(config["tool_version"])
    args = dict(config.get("args") or {})
    task_caps = config.get("task_caps")  # may be None or dict

    # ---- context unpacking -------------------------------------------
    app = context.get("app")
    fsf_registry = context.get("registry")
    if app is None or fsf_registry is None:
        return {
            "ok": False,
            "error": "scheduler context missing 'app' or 'registry'",
        }

    # ---- agent lookup ------------------------------------------------
    try:
        agent = fsf_registry.get_agent(agent_id)
    except Exception as e:
        return {
            "ok": False,
            "error": f"agent {agent_id!r} lookup failed: {type(e).__name__}: {e}",
        }

    # ---- dispatcher build/get ----------------------------------------
    # build_or_get_tool_dispatcher caches on app.state.tool_dispatcher
    # so this is amortized across all scheduled tool_call ticks.
    from forest_soul_forge.daemon.deps import (
        ToolDispatcherUnavailable,
        build_or_get_tool_dispatcher,
    )
    try:
        dispatcher = build_or_get_tool_dispatcher(app)
    except ToolDispatcherUnavailable as e:
        return {
            "ok": False,
            "error": f"dispatcher unavailable: {e}",
        }

    # ---- session_id rotation -----------------------------------------
    # ADR-0041 rate-limit open question: "scheduler rotates session
    # IDs daily (deterministic: <task_id>-<YYYYMMDD>)". task_id isn't
    # in the config dict directly — the scheduler doesn't pass it
    # because runners are config-shape-driven. We compose a stable
    # equivalent from the most-stable config fields. Daily resolution
    # matches the open-question's recommendation.
    today_utc = datetime.now(timezone.utc).strftime("%Y%m%d")
    session_id = f"sched-{agent_id}-{tool_name}-{today_utc}"

    # ---- provider resolution -----------------------------------------
    # llm_think and other model-using tools need a provider on the
    # ToolContext. Mirror the chat / tool-dispatch / skills-run /
    # pending-calls path's _resolve_active_provider helper:
    # app.state.providers.active() is the canonical accessor (a
    # ProviderRegistry, not a single provider object on app.state).
    #
    # Bug fix B144 (2026-05-05): the prior `getattr(app.state,
    # "active_provider", None)` referenced an attribute that's never
    # set anywhere — the daemon's lifespan stores the registry as
    # `app.state.providers`, not `app.state.active_provider`. This
    # silently returned None on every dispatch, surfacing as
    # ToolValidationError("no LLM provider wired into this dispatcher")
    # inside llm_think.v1's validate(). Surfaced live 2026-05-05 by
    # the freshly-activated specialist-stable scheduled tasks via
    # B141 + the B142 fix that finally let the real exception type
    # bubble out (instead of crashing the runner with AttributeError
    # on outcome.reason).
    providers = getattr(app.state, "providers", None)
    if providers is None:
        provider = None
    else:
        try:
            provider = providers.active()
        except Exception:
            provider = None

    # ---- write-lock + dispatch ---------------------------------------
    # Single-writer SQLite discipline. The dispatcher mutates registry
    # state (counters, pending_approvals, tool_calls); we hold the
    # write_lock for the duration of the dispatch, same as every HTTP
    # write path.
    write_lock = getattr(app.state, "write_lock", None)
    if write_lock is None:
        return {
            "ok": False,
            "error": "scheduler context missing 'write_lock'",
        }

    from forest_soul_forge.tools.dispatcher import (
        DispatchFailed,
        DispatchPendingApproval,
        DispatchRefused,
        DispatchSucceeded,
    )

    constitution_path = Path(agent.constitution_path)
    try:
        with write_lock:
            outcome = await dispatcher.dispatch(
                instance_id=agent.instance_id,
                agent_dna=agent.dna,
                role=agent.role,
                genre=None,
                session_id=session_id,
                constitution_path=constitution_path,
                tool_name=tool_name,
                tool_version=tool_version,
                args=args,
                provider=provider,
                task_caps=task_caps,
            )
    except Exception as e:  # pragma: no cover — dispatcher catches its own
        logger.exception("scheduler tool_call dispatch raised")
        return {
            "ok": False,
            "error": f"dispatch raised: {type(e).__name__}: {e}",
        }

    # ---- outcome interpretation --------------------------------------
    if isinstance(outcome, DispatchSucceeded):
        return {
            "ok": True,
            "agent_id": agent.instance_id,
            "tool": f"{tool_name}.v{tool_version}",
            "session_id": session_id,
            "tokens_used": outcome.result.tokens_used,
            "result_digest": outcome.result.result_digest,
        }
    if isinstance(outcome, DispatchRefused):
        return {
            "ok": False,
            "error": f"dispatch refused: {outcome.reason} ({outcome.detail})",
            "session_id": session_id,
        }
    if isinstance(outcome, DispatchPendingApproval):
        # ADR-0041 open-question (a): scheduler refuses tools that
        # require human approval. Treating pending as failure means
        # the circuit breaker eventually trips, surfacing the
        # misconfiguration to the operator instead of silently
        # piling rows in the approval queue.
        return {
            "ok": False,
            "error": (
                "tool requires human approval — scheduler tasks may "
                f"only invoke read_only-class tools (got {tool_name!r})"
            ),
            "session_id": session_id,
        }
    if isinstance(outcome, DispatchFailed):
        # Bug fix B142 (2026-05-05): DispatchFailed exposes
        # tool_key / exception_type / audit_seq — there is no .reason
        # attribute. The earlier `outcome.reason` reference raised
        # AttributeError every time a dispatch crashed, masking the
        # real failure with a runner crash and silently tripping
        # the scheduler's circuit breaker after 3 hits.
        # Surfaced 2026-05-05 by activated-scheduled-tasks live test
        # — dashboard_watcher_healthz_5m tripped within 15 minutes
        # of going live. See _diagnostic_err_dump.txt for the
        # original traceback.
        return {
            "ok": False,
            "error": (
                f"dispatch failed: {outcome.exception_type} "
                f"(tool={outcome.tool_key}, audit_seq={outcome.audit_seq})"
            ),
            "session_id": session_id,
        }
    # Defensive fallthrough — dispatch should always return one of the
    # four known outcome types. If it doesn't, that's a dispatcher bug.
    return {
        "ok": False,
        "error": f"unknown dispatch outcome type: {type(outcome).__name__}",
    }
