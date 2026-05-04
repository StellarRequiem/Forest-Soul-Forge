"""Task-type runners for the ADR-0041 scheduler.

Each runner is an async callable matching :data:`Scheduler.TaskRunner`::

    async def runner(config: dict, context: dict) -> dict

* ``config`` is the per-task ``config:`` block from
  ``scheduled_tasks.yaml``.
* ``context`` is the scheduler's process-wide context dict — built
  in the daemon's lifespan (see ``daemon/app.py``). Holds references
  to ``registry``, ``audit_chain``, ``settings``, and (per Burst 89)
  the FastAPI ``app`` itself for accessing other lazily-built
  subsystems.
* The runner returns ``{"ok": True, ...}`` on success or
  ``{"ok": False, "error": "..."}`` on failure. Anything else is
  treated as a runtime bug and surfaces as a failure with the
  message captured.

Task types currently registered:

* ``tool_call`` — dispatch one tool call against an existing agent.
  ADR-0036 T4 verifier scans use this. Closes the original deferral.

Future task types (per ADR-0041):

* ``scenario`` — multi-step birth + seed + iterate + archive
  scenario. Lands in Burst 90.
"""
from forest_soul_forge.daemon.scheduler.task_types.scenario import (
    scenario_runner,
)
from forest_soul_forge.daemon.scheduler.task_types.tool_call import (
    tool_call_runner,
)

__all__ = ["scenario_runner", "tool_call_runner"]
