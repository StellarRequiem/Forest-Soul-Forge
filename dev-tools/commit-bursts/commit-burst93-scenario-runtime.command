#!/usr/bin/env bash
# Burst 93: ADR-0041 T4 — scenario task type runtime.
#
# Closes ADR-0041 to v0.4.0 final (modulo the FizzBuzz YAML port,
# which is its own burst). After this commit, operators can ship
# multi-step YAML-driven scenarios alongside the v0.4.0-rc
# tool_call task type.
#
# WHAT'S NEW
#
# 1. daemon/scheduler/scenario_runtime.py — runtime substrate:
#    - load_scenario(path) — YAML loader + structural validation.
#      Required top-level keys: name, steps. Optional: description,
#      inputs (required/optional lists), defaults.
#    - ScenarioSpec dataclass holds the parsed structure.
#    - interpolate(value, ctx) — recursive ${var} interpolation.
#      Single-var case preserves typed values (max_turns: ${var}
#      yields int when var is int). Embedded case stringifies.
#      Dotted paths walk nested dicts (${result.output.response}).
#      Missing variables raise ScenarioError — fail-fast over
#      silent-empty-string.
#    - _evaluate_stop_when(conditions, ctx) — supports var_truthy
#      and var_equals shapes. Unknown shapes raise ScenarioError.
#    - ScenarioRuntime — stateful per-tick executor. Holds app +
#      registry + base_dir + scenario_name + started_at. Tracks
#      steps_executed for the outcome.
#    - 4 step types: read_file, write_file, dispatch_tool, iterate.
#    - _STEP_DISPATCH table — adding a new step type is one entry.
#
# 2. daemon/scheduler/task_types/scenario.py — async scenario_runner
#    matching the Scheduler's TaskRunner contract. Validates config,
#    merges inputs with defaults, checks required inputs present,
#    delegates execution to ScenarioRuntime. Returns
#    {ok: True, scenario, steps_executed, exit_reason} on success
#    or {ok: False, error} on validation/dispatch failure. Pure
#    outcome reporter — never raises out.
#
#    Path resolution: step-level relative paths anchor to the
#    scenario YAML's parent directory, NOT cwd. A scenario at
#    config/scenarios/foo.yaml writing "out.txt" writes to
#    config/scenarios/out.txt regardless of where the daemon was
#    launched. Less surprising, more portable.
#
# 3. daemon/scheduler/task_types/__init__.py — exports scenario_runner
#    alongside tool_call_runner.
#
# 4. daemon/app.py lifespan — registers the runner:
#      scheduler.register_task_type("scenario", scenario_runner)
#
# 5. tests/unit/test_scenario_runtime.py — 34 new unit tests:
#    - YAML loader: minimal/with-inputs/missing-file/missing-name/
#      empty-steps/non-mapping (6)
#    - Interpolation: single-var typed, embedded stringify, dotted
#      path, list/dict recursion, missing-var, missing-in-embedded,
#      non-string passthrough (7)
#    - stop_when: var_truthy match/no-match, var_equals match,
#      unknown kind, malformed entry (5)
#    - File I/O steps: read_file, write_file, parent-dir creation,
#      interpolation, round-trip (5)
#    - Step framework: unknown kind, single-key requirement (2)
#    - iterate: max_turns countdown, var_truthy stop, max_turns
#      interpolation, max_turns must-be-int (4)
#    - Runner integration: missing config keys, missing context,
#      load failure, missing required input, full success path (5)
#
# WHAT'S DEFERRED
#
# - birth_agent step — operator pre-births the agent and passes
#   agent_id as a scenario input. Birthing inside a scenario
#   touches significant governance surface (constitution patches,
#   lifecycle audit events) and is gated until v0.5.
# - archive_agent step — same reasoning.
# - FizzBuzz YAML port — replaces live-test-fizzbuzz.command's
#   bash driver. Burst 94 candidate. The bash driver still works;
#   porting is value-add not load-bearing.
# - Cron schedules. Interval-only per ADR-0041.
#
# VERIFICATION
#
# Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
#   → 2163 passed, 3 skipped, 1 xfailed.
#   Was 2129 at v0.4.0-rc. +34 from this burst. Zero regressions.
#
# Host (post-restart):
#   curl -s http://127.0.0.1:7423/scheduler/status | python3 -m json.tool
# Should show:
#   "registered_runners": ["scenario", "tool_call"]
# (was ["tool_call"] at v0.4.0-rc).
#
# WHAT THIS CLOSES
#
# All 5 ADR-0041 tranches now landed:
#   T1 design (Burst 85)
#   T2 runtime + lifespan (Burst 86)
#   T3 tool_call task type + audit emit (Burst 89)
#   T4 scenario task type runtime ← this burst
#   T5 SQLite v13 persistence (Burst 90)
#   T6 operator control endpoints (Burst 91)
#
# v0.4.0 final waits on:
#   - FizzBuzz YAML port (Burst 94 candidate, optional)
#   - STATE/README/CHANGELOG refresh covering scenarios
#   - Tag v0.4.0 (replaces v0.4.0-rc)

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 93 — ADR-0041 T4: scenario task type runtime ==="
echo
clean_locks
git add src/forest_soul_forge/daemon/scheduler/scenario_runtime.py
git add src/forest_soul_forge/daemon/scheduler/task_types/scenario.py
git add src/forest_soul_forge/daemon/scheduler/task_types/__init__.py
git add src/forest_soul_forge/daemon/app.py
git add tests/unit/test_scenario_runtime.py
git add commit-burst93-scenario-runtime.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(scheduler): scenario task type runtime (ADR-0041 T4)

Closes the ADR-0041 arc to v0.4.0 final (modulo the FizzBuzz YAML
port, deferred to its own burst). Scenarios are YAML-driven
multi-step workflows registered as the second task type alongside
tool_call.

New module daemon/scheduler/scenario_runtime.py:
- load_scenario(path) parses + structurally validates a scenario
  YAML. Required top-level keys: name, steps. Optional: inputs
  (required/optional lists), defaults.
- interpolate(value, ctx) does recursive \${var} substitution.
  Single-var case preserves typed values (max_turns: \${var}
  yields int when var is int). Embedded case stringifies.
  Dotted paths walk nested dicts (\${result.output.response}).
  Missing variables raise ScenarioError — fail-fast over
  silent-empty-string.
- _evaluate_stop_when supports var_truthy and var_equals shapes.
- ScenarioRuntime is the stateful per-tick executor. Tracks
  steps_executed for the outcome shape.
- Four step types in v0.4: read_file, write_file, dispatch_tool,
  iterate (max_turns + stop_when). Adding a new step type is one
  entry in _STEP_DISPATCH; that's the deliberate extension shape.

New module daemon/scheduler/task_types/scenario.py — async
scenario_runner matching the Scheduler.TaskRunner contract.
Validates config (scenario_path required), loads spec, merges
inputs with defaults, checks required inputs present, delegates
to ScenarioRuntime. Returns {ok: True, scenario, steps_executed,
exit_reason} on success; {ok: False, error} on any failure.
Path resolution: step-level relative paths anchor to the scenario
YAML's parent directory, not cwd — keeps scenarios portable.

Lifespan registers the runner:
  scheduler.register_task_type('scenario', scenario_runner)

Tests +34 in test_scenario_runtime.py covering loader (6),
interpolation (7), stop_when (5), file I/O steps (5), step
framework (2), iterate (4), runner integration (5).

Deferred to later bursts:
- birth_agent step (operator pre-births; significant governance
  surface, gated until v0.5)
- archive_agent step (same reasoning)
- FizzBuzz YAML port (replaces the bash live-test driver — value-
  add not load-bearing; bash driver still works)
- Cron schedules (interval-only per ADR-0041)

Verification: 2129 → 2163 unit tests pass (+34). Zero regressions.

ADR-0041 tranches:
  T1 design (Burst 85) ✓
  T2 runtime + lifespan (Burst 86) ✓
  T3 tool_call task type + audit emit (Burst 89) ✓
  T4 scenario task type runtime (this burst) ✓
  T5 SQLite v13 persistence (Burst 90) ✓
  T6 operator control endpoints (Burst 91) ✓

All 5 implementation tranches landed. v0.4.0 final after the
FizzBuzz YAML port + STATE/README/CHANGELOG refresh."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 93 landed. ADR-0041 T4 scenario task type runtime is real."
echo "All 5 ADR-0041 implementation tranches now shipped."
echo ""
read -rp "Press Enter to close..."
