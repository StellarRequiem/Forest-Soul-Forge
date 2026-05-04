"""``scenario`` task type runner — multi-step YAML-driven scenarios.

ADR-0041 T4, Burst 93. Closes the orchestrator arc to v0.4.0 final.

A scenario is a YAML file with declared inputs and a list of steps.
The runner loads it, validates required inputs, runs the steps
sequentially, and returns a structured outcome the scheduler
records on the audit chain.

Config shape (one entry from ``scheduled_tasks.yaml``)::

    - id: fizzbuzz_smoke_6h
      type: scenario
      schedule: "every 6h"
      enabled: true
      config:
        scenario_path: config/scenarios/fizzbuzz.yaml
        inputs:
          agent_id: software_engineer_f782d3ed1b6b
          target_dir: data/test-runs/scheduled-fizzbuzz
          max_turns: 50

Scenario YAML shape::

    name: fizzbuzz
    description: "Stub-and-test FizzBuzz coding loop"
    inputs:
      required: [agent_id, target_dir]
      optional: [max_turns]
    defaults:
      max_turns: 50
    steps:
      - read_file:
          path: "${target_dir}/fizzbuzz.py"
          into: current_code
      - dispatch_tool:
          agent_id: "${agent_id}"
          tool: llm_think
          version: "1"
          args:
            prompt: "Complete this FizzBuzz: ${current_code}"
            max_tokens: 600
          into: llm_result
      - write_file:
          path: "${target_dir}/fizzbuzz.py"
          content: "${llm_result.output.response}"
      - iterate:
          max_turns: "${max_turns}"
          stop_when:
            - var_truthy: success
          step:
            - dispatch_tool:
                agent_id: "${agent_id}"
                tool: pytest_run
                version: "1"
                args:
                  target: "${target_dir}/test_fizzbuzz.py"
                into: test_result

Step types in v0.4-rc + this burst:

- ``read_file`` — reads a file into a context variable
- ``write_file`` — writes a context variable (or literal) to a file
- ``dispatch_tool`` — dispatches one tool call against an existing
  agent through the standard ToolDispatcher
- ``iterate`` — loops a sub-step list up to ``max_turns`` times,
  exiting early on any ``stop_when`` condition

What this burst does NOT do (deferred):

- ``birth_agent`` step — operator pre-births and passes agent_id
  as an input. Birthing inside a scenario adds significant
  governance surface (constitution patches, lifecycle audit
  events) and is gated until v0.5.
- ``archive_agent`` step — same reasoning.
- FizzBuzz YAML port — the live-test-fizzbuzz.command bash driver
  still runs the same flow; porting it to YAML is Burst 94.
- Cron schedules. Interval-only per ADR-0041.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.daemon.scheduler.scenario_runtime import (
    ScenarioError,
    ScenarioRuntime,
    load_scenario,
)

logger = logging.getLogger(__name__)


_REQUIRED_CONFIG_KEYS = ("scenario_path",)


async def scenario_runner(
    config: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute one scenario task tick.

    Returns ``{"ok": True, "scenario": name, "steps_executed": N,
    "exit_reason": "..."}`` on success, or
    ``{"ok": False, "error": "..."}`` on any validation/dispatch
    failure. Pure outcome reporter — never raises out of the
    runner; that's the scheduler's contract.
    """
    # ---- config validation -------------------------------------------
    missing = [k for k in _REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        return {
            "ok": False,
            "error": f"config missing required keys: {missing}",
        }
    scenario_path_str = str(config["scenario_path"])
    inputs = dict(config.get("inputs") or {})

    app = context.get("app")
    fsf_registry = context.get("registry")
    if app is None or fsf_registry is None:
        return {
            "ok": False,
            "error": "scheduler context missing 'app' or 'registry'",
        }
    settings = context.get("settings")
    repo_root: Path
    if settings is not None and getattr(settings, "repo_root", None):
        repo_root = Path(settings.repo_root)
    else:
        repo_root = Path.cwd()

    scenario_path = Path(scenario_path_str)
    if not scenario_path.is_absolute():
        scenario_path = repo_root / scenario_path

    # Step-level relative paths (read_file/write_file) anchor to the
    # scenario YAML's parent directory, NOT cwd. Scenarios should be
    # self-contained — a scenario YAML at config/scenarios/foo.yaml
    # that writes "out.txt" writes to config/scenarios/out.txt, not
    # wherever the daemon happened to be launched from. This is the
    # less surprising default and keeps scenarios portable.
    base_dir = scenario_path.parent.resolve()

    # ---- load + validate ---------------------------------------------
    try:
        scenario = load_scenario(scenario_path)
    except ScenarioError as e:
        return {"ok": False, "error": f"scenario load failed: {e}"}

    # ---- merge inputs with defaults ----------------------------------
    merged_inputs: dict[str, Any] = {}
    merged_inputs.update(scenario.defaults)
    merged_inputs.update(inputs)
    missing_required = [
        k for k in scenario.required_inputs if k not in merged_inputs
    ]
    if missing_required:
        return {
            "ok": False,
            "error": (
                f"scenario {scenario.name!r} missing required inputs: "
                f"{missing_required}"
            ),
        }

    # ---- execute -----------------------------------------------------
    runtime = ScenarioRuntime(
        app=app,
        registry=fsf_registry,
        base_dir=base_dir,
        scenario_name=scenario.name,
        started_at=datetime.now(timezone.utc),
    )
    try:
        result = await runtime.execute(scenario.steps, dict(merged_inputs))
    except ScenarioError as e:
        return {
            "ok": False,
            "error": f"scenario {scenario.name!r} step failed: {e}",
            "scenario": scenario.name,
            "steps_executed": runtime.steps_executed,
        }
    except Exception as e:  # pragma: no cover — defensive
        logger.exception("scenario runner raised")
        return {
            "ok": False,
            "error": f"scenario {scenario.name!r} raised: {type(e).__name__}: {e}",
            "scenario": scenario.name,
            "steps_executed": runtime.steps_executed,
        }

    return {
        "ok": True,
        "scenario": scenario.name,
        "steps_executed": runtime.steps_executed,
        "exit_reason": result.get("exit_reason", "completed"),
    }
