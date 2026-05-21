"""``memory_consolidation`` task type runner — ADR-0074 T4.

Scheduler runner that executes one end-to-end memory-consolidation
pass: select aged-out pending entries, summarize each (agent, layer)
batch via the active provider, fold the sources into the summary,
and emit the ADR-0074 bookend + per-entry audit events.

The end-to-end pass lives in
``core/memory_consolidation.run_consolidation_pass``; this module is
the thin daemon-side wrapper that resolves the registry connection,
the active provider, and the write lock from the scheduler context.

Config shape (one entry from ``scheduled_tasks.yaml``)::

    - id: memory_consolidation_nightly
      description: "Fold aged-out episodic memories into summaries"
      schedule: "every 24h"
      enabled: true
      type: memory_consolidation
      config:
        min_age_days:         14                  # optional
        max_batch_size:       200                 # optional
        eligible_layers:      [episodic]          # optional
        eligible_claim_types: [observation, user_statement]  # optional

All config keys are optional; omitting them uses the
:class:`ConsolidationPolicy` defaults. The audit events
(``memory_consolidation_run_started`` / ``memory_consolidated`` /
``memory_consolidation_run_completed``) are emitted by
``run_consolidation_pass`` itself — this wrapper adds none.
"""
from __future__ import annotations

import logging
from typing import Any

from forest_soul_forge.core.memory_consolidation import (
    ConsolidationPolicy,
    run_consolidation_pass,
)

logger = logging.getLogger(__name__)


async def memory_consolidation_runner(
    config: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Run one consolidation pass. See module docstring for config.

    Pure outcome reporter — never raises. Hard failures (missing
    context, invalid policy) surface as ``{"ok": False, "error":
    ...}``. A completed pass returns ``{"ok": True, ...}`` even when
    individual (agent, layer) groups errored — per-group failures are
    normal outcomes carried in ``errors``, not runner failures
    (same posture as :func:`learned_rule_ra_pass_runner`).
    """
    # ---- context unpacking -------------------------------------------
    app = context.get("app")
    registry = context.get("registry")
    audit_chain = context.get("audit_chain")
    if app is None or registry is None:
        return {
            "ok": False,
            "error": "scheduler context missing 'app' or 'registry'",
        }

    write_lock = getattr(app.state, "write_lock", None)
    if write_lock is None:
        return {
            "ok": False,
            "error": "scheduler context missing 'write_lock'",
        }

    # ---- policy ------------------------------------------------------
    # Every key is optional — fall back to ConsolidationPolicy
    # defaults. ConsolidationPolicy.__post_init__ validates the
    # numeric knobs and raises ValueError on bad input.
    policy_kwargs: dict[str, Any] = {}
    try:
        if "min_age_days" in config:
            policy_kwargs["min_age_days"] = int(config["min_age_days"])
        if "max_batch_size" in config:
            policy_kwargs["max_batch_size"] = int(config["max_batch_size"])
        if "eligible_layers" in config:
            policy_kwargs["eligible_layers"] = tuple(config["eligible_layers"])
        if "eligible_claim_types" in config:
            policy_kwargs["eligible_claim_types"] = tuple(
                config["eligible_claim_types"]
            )
        policy = ConsolidationPolicy(**policy_kwargs)
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": f"invalid consolidation policy: {e}"}

    # ---- provider resolution -----------------------------------------
    # summarize_consolidation_batch calls provider.complete with
    # TaskKind.GENERATE. app.state.providers is the canonical
    # ProviderRegistry accessor (see tool_call_runner / B144). A None
    # provider is survivable — run_consolidation_pass records it as a
    # per-group error and the pass still completes.
    providers = getattr(app.state, "providers", None)
    provider = None
    if providers is not None:
        try:
            provider = providers.active()
        except Exception:  # noqa: BLE001
            provider = None

    # ---- run the pass under the write lock ---------------------------
    # run_consolidation_pass does its own per-group SQL transactions
    # but acquires no lock — its docstring delegates single-writer
    # serialization to the caller. Hold write_lock around the pass,
    # the same posture tool_call_runner uses around dispatcher.dispatch.
    conn = registry._conn  # noqa: SLF001 — daemon/app.py uses the same accessor
    try:
        with write_lock:
            result = await run_consolidation_pass(
                conn,
                policy=policy,
                provider=provider,
                audit_chain=audit_chain,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("memory_consolidation pass raised")
        return {
            "ok": False,
            "error": f"pass raised: {type(e).__name__}: {e}",
        }

    return {
        "ok":                   True,
        "run_id":               result.run_id,
        "batches_processed":    result.batches_processed,
        "summaries_created":    result.summaries_created,
        "sources_consolidated": result.sources_consolidated,
        "errors":               [list(e) for e in result.errors],
        "started_at":           result.started_at,
        "completed_at":         result.completed_at,
    }
