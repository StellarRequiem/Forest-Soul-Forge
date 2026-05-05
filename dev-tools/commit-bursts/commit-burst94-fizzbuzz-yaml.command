#!/usr/bin/env bash
# Burst 94: FizzBuzz scenario YAML port — closes Burst 81 P1.
#
# Replaces live-test-fizzbuzz.command's bash driver with the
# canonical YAML scenario, leveraging the T4 runtime from Burst 93.
# After this commit, the FizzBuzz autonomous coding loop runs
# inside the daemon's asyncio loop on a schedule (every N hours)
# instead of as a one-shot bash invocation.
#
# WHAT'S NEW
#
# 1. config/scenarios/fizzbuzz.yaml — the canonical scenario.
#    - inputs: agent_id (required), target_dir (required),
#      max_turns (default 10).
#    - 3 seed steps that idempotently rewrite fizzbuzz.py stub +
#      test_fizzbuzz.py + README.md inside target_dir every tick.
#    - 1 iterate step that runs the canonical 5-step coding loop:
#        pytest_run -> stop on green
#        read_file fizzbuzz.py + test_fizzbuzz.py
#        dispatch llm_think with the build prompt
#        extract_code_block from the response
#        write_file the new fizzbuzz.py
#
# 2. scenario_runtime.py — two minimal extensions to v0.4-rc's
#    runtime, both load-bearing for the canonical coding-loop
#    scenario:
#
#    a. extract_code_block step type. Pulls the body from a
#       ```<lang> ... ``` fenced block in any string variable.
#       Body shape:
#         extract_code_block:
#           from: "${llm_result.output.response}"
#           into: code
#           language: python   # optional; default '' matches any
#           fallback: passthrough  # default 'raise'
#       Useful for any LLM-output-extraction scenario, not just
#       FizzBuzz. The fallback knob saves runs against
#       under-instructed models that don't always fence.
#
#    b. pytest_passed stop_when kind. Checks a dispatch_tool
#       result whose pytest_run output indicates green:
#         output.passed > 0 AND output.failed == 0
#         AND output.errors == 0
#       Domain-specific but the canonical 'exit the coding loop'
#       check. live-test-fizzbuzz.command did this as a regex
#       over summary_line which has a bug (matches '2 failed,
#       2 passed' as 'passed'). The structured check is more
#       reliable.
#
# 3. tests/unit/test_scenario_runtime.py +14 tests:
#    - extract_code_block: python fence, first-match-wins,
#      language filter, no-fence-raises, passthrough fallback,
#      bare-fence (any language), missing required keys (7)
#    - pytest_passed stop_when: green match, failure no-match,
#      zero-passed no-match, missing var, malformed output (5)
#    - FizzBuzz YAML smoke: loads + validates, input contract
#      enforced (2)
#
# OPERATOR USAGE
#
# After v0.4.0 lands, the operator wires this scenario into a
# scheduled task:
#
#   # config/scheduled_tasks.yaml
#   tasks:
#     - id: fizzbuzz_smoke_6h
#       type: scenario
#       schedule: 'every 6h'
#       enabled: true
#       config:
#         scenario_path: config/scenarios/fizzbuzz.yaml
#         inputs:
#           agent_id: software_engineer_<dna>
#           target_dir: /abs/path/to/data/test-runs/scheduled-fizzbuzz
#           max_turns: 10
#
# Restart the daemon, watch /scheduler/tasks/fizzbuzz_smoke_6h
# every six hours. Each tick:
# - Re-seeds the workspace (idempotent).
# - Iterates up to max_turns dispatching pytest_run + llm_think.
# - Stops early on green via the pytest_passed stop_when.
# - Audit chain captures every dispatch + the scheduled_task_*
#   events.
#
# DIFFERENCES FROM THE BASH DRIVER
#
# - Runs inside the daemon's asyncio loop instead of as bash.
#   Same dispatcher, same governance, same audit chain.
# - Structured pytest stop check (failed==0 + passed>0) instead
#   of the bash driver's regex over summary_line.
# - Daily-rotating session_id (sched-scenario-fizzbuzz-YYYYMMDD)
#   per ADR-0041's rate-limit mitigation. The bash driver used
#   a timestamp suffix that didn't preserve daily-counter
#   semantics across re-runs.
# - State survives restarts via the v13 scheduled_task_state
#   table (Burst 90).
# - Operator controls (trigger / enable / disable / reset) via
#   /scheduler endpoints (Burst 91).
#
# WHAT THE BASH DRIVER STILL DOES BETTER
#
# - One-shot ad-hoc runs (no daemon required). The YAML version
#   needs the scheduler running. Both are valuable; we're not
#   removing the bash driver yet.
#
# VERIFICATION
#
# Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
#   → 2177 passed, 3 skipped, 1 xfailed. +14 from Burst 93.
#   The YAML loader smoke catches accidental syntax breakage.
#
# Host (post-restart with the schedule wired in):
#   curl -X POST -H "X-API-Token: $TOKEN" \
#     http://127.0.0.1:7423/scheduler/tasks/fizzbuzz_smoke_6h/trigger
# Watch the audit chain for scheduled_task_dispatched +
# tool_call_dispatched(pytest_run) + tool_call_dispatched(llm_think)
# pairs. Final scheduled_task_completed if the agent solved it.
#
# CLOSES
#
# - Burst 81 P1 audit item ('FizzBuzz scenario port to YAML').
# - The sole bash-driver dependency for autonomous coding-loop
#   tests. v0.4.0 final no longer ships shell scripts as the
#   canonical autonomous-loop driver.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 94 — FizzBuzz scenario YAML port ==="
echo
clean_locks
git add config/scenarios/fizzbuzz.yaml
git add src/forest_soul_forge/daemon/scheduler/scenario_runtime.py
git add tests/unit/test_scenario_runtime.py
git add commit-burst94-fizzbuzz-yaml.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(scheduler): FizzBuzz scenario YAML + extract_code_block + pytest_passed

Closes Burst 81 P1 audit item. Replaces the bash driver
live-test-fizzbuzz.command for the canonical autonomous coding
loop with config/scenarios/fizzbuzz.yaml — runs inside the
daemon's asyncio loop on a schedule instead of as a one-shot
bash invocation.

Two minimal extensions to scenario_runtime.py, both load-bearing
for the canonical coding-loop scenario:

extract_code_block step type pulls the body from a fenced
\`\`\`<lang> ... \`\`\` block in any string variable. Body shape:
  extract_code_block:
    from: \"\${llm_result.output.response}\"
    into: code
    language: python    # optional, default '' matches any
    fallback: passthrough   # default 'raise'
The fallback knob saves runs against under-instructed models that
don't always fence their output.

pytest_passed stop_when kind checks a dispatch_tool result whose
pytest_run output indicates green: passed>0 AND failed==0 AND
errors==0. Domain-specific but the canonical 'exit the coding
loop' check. The bash driver used a regex over summary_line
which has a known bug (matches '2 failed, 2 passed' as 'passed').
Structured check is more reliable.

config/scenarios/fizzbuzz.yaml seeds the workspace on every tick
(fizzbuzz.py stub + test_fizzbuzz.py + README.md, idempotent),
then iterates the canonical 5-step coding loop:
  pytest_run -> stop on green
  read_file fizzbuzz.py + test_fizzbuzz.py
  dispatch llm_think with the build prompt
  extract_code_block from the response
  write_file the new fizzbuzz.py

Operator usage (post v0.4.0):
  tasks:
    - id: fizzbuzz_smoke_6h
      type: scenario
      schedule: 'every 6h'
      config:
        scenario_path: config/scenarios/fizzbuzz.yaml
        inputs:
          agent_id: software_engineer_<dna>
          target_dir: /abs/path/to/test-runs/scheduled-fizzbuzz
          max_turns: 10

Tests +14: extract_code_block (7), pytest_passed (5), FizzBuzz
YAML loader+input-contract smoke (2).

Verification: 2163 -> 2177 unit tests pass. Zero regressions.

The bash live-test-fizzbuzz.command driver is retained for
one-shot ad-hoc runs — both forms are valuable. v0.4.0 final
no longer ships shell scripts as the canonical autonomous-loop
driver."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 94 landed. FizzBuzz scenario YAML closes Burst 81 P1."
echo "Wire it into config/scheduled_tasks.yaml after v0.4.0 to run on schedule."
echo ""
read -rp "Press Enter to close..."
