"""Unit tests for ADR-0041 T4 scenario runtime.

Coverage targets:
- Scenario YAML loader (valid + malformed inputs)
- Variable interpolation (single-var typed, multi-var stringified,
  dotted paths, missing-var errors)
- stop_when conditions (var_truthy, var_equals)
- Step handlers in isolation: read_file, write_file, iterate
- The runner-level error mapping ({ok: False, error: ...})

dispatch_tool's full path is exercised at integration level via
the existing tool_call_runner tests; this file's dispatch_tool
test verifies the runtime's plumbing (agent lookup, dispatcher
build) without spinning a full daemon.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from forest_soul_forge.daemon.scheduler.scenario_runtime import (
    ScenarioError,
    ScenarioRuntime,
    _evaluate_stop_when,
    interpolate,
    load_scenario,
)


# ---- Loader -------------------------------------------------------------

def _write_scenario(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "scenario.yaml"
    p.write_text(body)
    return p


def test_load_scenario_minimal(tmp_path: Path):
    p = _write_scenario(tmp_path, """
name: test_min
description: minimal scenario
steps:
  - read_file:
      path: foo.txt
      into: contents
""")
    spec = load_scenario(p)
    assert spec.name == "test_min"
    assert spec.description == "minimal scenario"
    assert len(spec.steps) == 1
    assert spec.required_inputs == []


def test_load_scenario_with_inputs_and_defaults(tmp_path: Path):
    p = _write_scenario(tmp_path, """
name: test_inputs
inputs:
  required: [agent_id]
  optional: [max_turns]
defaults:
  max_turns: 50
steps:
  - read_file:
      path: foo.txt
      into: x
""")
    spec = load_scenario(p)
    assert spec.required_inputs == ["agent_id"]
    assert spec.optional_inputs == ["max_turns"]
    assert spec.defaults == {"max_turns": 50}


def test_load_scenario_missing_file(tmp_path: Path):
    with pytest.raises(ScenarioError, match="not found"):
        load_scenario(tmp_path / "nope.yaml")


def test_load_scenario_missing_name(tmp_path: Path):
    p = _write_scenario(tmp_path, "steps:\n  - read_file: {path: x, into: y}\n")
    with pytest.raises(ScenarioError, match="missing required 'name'"):
        load_scenario(p)


def test_load_scenario_empty_steps(tmp_path: Path):
    p = _write_scenario(tmp_path, "name: x\nsteps: []\n")
    with pytest.raises(ScenarioError, match="non-empty list"):
        load_scenario(p)


def test_load_scenario_not_a_mapping(tmp_path: Path):
    p = _write_scenario(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ScenarioError, match="YAML mapping"):
        load_scenario(p)


# ---- Interpolation -------------------------------------------------------

def test_interpolate_single_var_preserves_type():
    ctx = {"max_turns": 50}
    assert interpolate("${max_turns}", ctx) == 50  # int, not "50"


def test_interpolate_embedded_stringifies():
    ctx = {"name": "fizzbuzz"}
    assert interpolate("scenario_${name}_run", ctx) == "scenario_fizzbuzz_run"


def test_interpolate_dotted_path():
    ctx = {"result": {"output": {"response": "hello"}}}
    assert interpolate("${result.output.response}", ctx) == "hello"


def test_interpolate_recurses_into_list_and_dict():
    ctx = {"x": 1, "y": "two"}
    out = interpolate(["${x}", {"k": "${y}"}, "literal"], ctx)
    assert out == [1, {"k": "two"}, "literal"]


def test_interpolate_missing_var_raises():
    with pytest.raises(ScenarioError, match="not in context"):
        interpolate("${missing}", {})


def test_interpolate_missing_var_in_embedded_raises():
    with pytest.raises(ScenarioError, match="not in context"):
        interpolate("hello-${missing}-world", {})


def test_interpolate_passthrough_for_non_string():
    assert interpolate(42, {}) == 42
    assert interpolate(True, {}) is True
    assert interpolate(None, {}) is None


# ---- stop_when ----------------------------------------------------------

def test_stop_when_var_truthy_matches():
    match = _evaluate_stop_when([{"var_truthy": "success"}], {"success": True})
    assert match == "var_truthy:success"


def test_stop_when_var_truthy_no_match():
    assert _evaluate_stop_when([{"var_truthy": "success"}], {"success": False}) is None
    assert _evaluate_stop_when([{"var_truthy": "success"}], {}) is None


def test_stop_when_var_equals_matches():
    match = _evaluate_stop_when(
        [{"var_equals": {"var": "code", "value": 0}}],
        {"code": 0},
    )
    assert match.startswith("var_equals:code=")


def test_stop_when_unknown_kind_raises():
    with pytest.raises(ScenarioError, match="unknown stop_when"):
        _evaluate_stop_when([{"never_stop": "x"}], {})


def test_stop_when_malformed_entry_raises():
    with pytest.raises(ScenarioError, match="single-key dict"):
        _evaluate_stop_when([{"a": 1, "b": 2}], {})


# ---- Step handlers (file I/O) -------------------------------------------

def _runtime(tmp_path: Path) -> ScenarioRuntime:
    return ScenarioRuntime(
        app=None,
        registry=None,
        base_dir=tmp_path,
        scenario_name="test",
        started_at=datetime.now(timezone.utc),
    )


def test_read_file_step(tmp_path: Path):
    (tmp_path / "input.txt").write_text("hello world")
    rt = _runtime(tmp_path)
    ctx: dict = {}
    asyncio.run(rt.execute([
        {"read_file": {"path": "input.txt", "into": "contents"}}
    ], ctx))
    assert ctx["contents"] == "hello world"
    assert rt.steps_executed == 1


def test_write_file_step(tmp_path: Path):
    rt = _runtime(tmp_path)
    asyncio.run(rt.execute([
        {"write_file": {"path": "out.txt", "content": "from-scenario"}}
    ], {}))
    assert (tmp_path / "out.txt").read_text() == "from-scenario"


def test_write_file_creates_parent_dir(tmp_path: Path):
    rt = _runtime(tmp_path)
    asyncio.run(rt.execute([
        {"write_file": {"path": "deep/nested/path.txt", "content": "x"}}
    ], {}))
    assert (tmp_path / "deep/nested/path.txt").read_text() == "x"


def test_read_file_uses_interpolation(tmp_path: Path):
    (tmp_path / "z.txt").write_text("payload")
    rt = _runtime(tmp_path)
    ctx = {"name": "z"}
    asyncio.run(rt.execute([
        {"read_file": {"path": "${name}.txt", "into": "x"}}
    ], ctx))
    assert ctx["x"] == "payload"


def test_write_then_read_round_trip(tmp_path: Path):
    rt = _runtime(tmp_path)
    ctx = {"payload": "hello-loop"}
    asyncio.run(rt.execute([
        {"write_file": {"path": "rt.txt", "content": "${payload}"}},
        {"read_file": {"path": "rt.txt", "into": "got"}},
    ], ctx))
    assert ctx["got"] == "hello-loop"


def test_unknown_step_kind_raises(tmp_path: Path):
    rt = _runtime(tmp_path)
    with pytest.raises(ScenarioError, match="unknown step type"):
        asyncio.run(rt.execute([{"nonsense_step": {}}], {}))


def test_step_must_be_single_key(tmp_path: Path):
    rt = _runtime(tmp_path)
    with pytest.raises(ScenarioError, match="single-key mapping"):
        asyncio.run(rt.execute([{"read_file": {}, "write_file": {}}], {}))


# ---- iterate ------------------------------------------------------------

def test_iterate_runs_max_turns(tmp_path: Path):
    rt = _runtime(tmp_path)
    ctx: dict = {"counter": 0}
    asyncio.run(rt.execute([
        {"iterate": {
            "max_turns": 3,
            "step": [
                {"write_file": {
                    "path": "tick-${_iterate_turn}.txt",
                    "content": "ok",
                }},
            ],
        }},
    ], ctx))
    assert (tmp_path / "tick-0.txt").exists()
    assert (tmp_path / "tick-1.txt").exists()
    assert (tmp_path / "tick-2.txt").exists()
    assert not (tmp_path / "tick-3.txt").exists()
    assert ctx["_iterate_exit_reason"] == "max_turns:3"


def test_iterate_stops_on_var_truthy(tmp_path: Path):
    rt = _runtime(tmp_path)
    ctx: dict = {"done": False}

    # Pre-write the trigger files so the read step can flip 'done'
    # only on iteration 2.
    (tmp_path / "step0.txt").write_text("")
    (tmp_path / "step1.txt").write_text("yes")

    # Use a custom step list that flips 'done' to truthy after turn 1.
    asyncio.run(rt.execute([
        {"iterate": {
            "max_turns": 10,
            "stop_when": [{"var_truthy": "done"}],
            "step": [
                {"read_file": {"path": "step${_iterate_turn}.txt", "into": "done"}},
            ],
        }},
    ], ctx))
    # Iteration 0 read empty, iteration 1 read "yes" → stop.
    assert ctx["_iterate_exit_reason"] == "var_truthy:done"
    # _iterate_turn was 1 when the stop check fired.
    assert ctx["_iterate_turn"] == 1


def test_iterate_max_turns_interpolates(tmp_path: Path):
    """max_turns: ${var} should resolve to the int from context."""
    rt = _runtime(tmp_path)
    ctx: dict = {"limit": 2}
    asyncio.run(rt.execute([
        {"iterate": {
            "max_turns": "${limit}",
            "step": [
                {"write_file": {"path": "i-${_iterate_turn}.txt", "content": "x"}}
            ],
        }},
    ], ctx))
    assert (tmp_path / "i-0.txt").exists()
    assert (tmp_path / "i-1.txt").exists()
    assert not (tmp_path / "i-2.txt").exists()


def test_iterate_max_turns_must_be_int(tmp_path: Path):
    rt = _runtime(tmp_path)
    with pytest.raises(ScenarioError, match="must be an int"):
        asyncio.run(rt.execute([
            {"iterate": {"max_turns": "not_a_number", "step": []}}
        ], {}))


# ---- Runner integration (the full scenario_runner path) ----------------

def test_scenario_runner_missing_required_keys():
    from forest_soul_forge.daemon.scheduler.task_types.scenario import (
        scenario_runner,
    )
    out = asyncio.run(scenario_runner({}, {}))
    assert out["ok"] is False
    assert "scenario_path" in out["error"]


def test_scenario_runner_missing_context():
    from forest_soul_forge.daemon.scheduler.task_types.scenario import (
        scenario_runner,
    )
    out = asyncio.run(scenario_runner({"scenario_path": "x"}, {}))
    assert out["ok"] is False
    assert "missing 'app' or 'registry'" in out["error"]


def test_scenario_runner_load_failure(tmp_path: Path):
    from forest_soul_forge.daemon.scheduler.task_types.scenario import (
        scenario_runner,
    )
    out = asyncio.run(scenario_runner(
        {"scenario_path": str(tmp_path / "missing.yaml")},
        {"app": object(), "registry": object()},
    ))
    assert out["ok"] is False
    assert "scenario load failed" in out["error"]


def test_scenario_runner_missing_required_input(tmp_path: Path):
    from forest_soul_forge.daemon.scheduler.task_types.scenario import (
        scenario_runner,
    )
    p = _write_scenario(tmp_path, """
name: needs_agent
inputs:
  required: [agent_id]
steps:
  - read_file: {path: x, into: y}
""")
    out = asyncio.run(scenario_runner(
        {"scenario_path": str(p)},
        {"app": object(), "registry": object()},
    ))
    assert out["ok"] is False
    assert "missing required inputs" in out["error"]
    assert "agent_id" in out["error"]


def test_scenario_runner_full_success_path(tmp_path: Path):
    """End-to-end: a scenario that just does file I/O succeeds with
    the right outcome shape."""
    from forest_soul_forge.daemon.scheduler.task_types.scenario import (
        scenario_runner,
    )
    (tmp_path / "src.txt").write_text("payload")
    p = _write_scenario(tmp_path, """
name: file_round_trip
inputs:
  required: [src]
  optional: []
steps:
  - read_file:
      path: ${src}
      into: contents
  - write_file:
      path: out.txt
      content: ${contents}
""")
    out = asyncio.run(scenario_runner(
        {"scenario_path": str(p), "inputs": {"src": str(tmp_path / "src.txt")}},
        {"app": object(), "registry": object()},
    ))
    assert out["ok"] is True
    assert out["scenario"] == "file_round_trip"
    assert out["steps_executed"] == 2
    assert out["exit_reason"] == "completed"
    assert (tmp_path / "out.txt").read_text() == "payload"


# ---- Burst 94: extract_code_block + pytest_passed + FizzBuzz YAML -------

def test_extract_code_block_python(tmp_path: Path):
    """Pull the body out of a ```python``` fence."""
    rt = _runtime(tmp_path)
    ctx: dict = {}
    asyncio.run(rt.execute([
        {"extract_code_block": {
            "from": "Here is your code:\n```python\ndef foo():\n    return 1\n```\nDone.",
            "into": "code",
            "language": "python",
        }}
    ], ctx))
    assert ctx["code"] == "def foo():\n    return 1"


def test_extract_code_block_first_match_wins(tmp_path: Path):
    rt = _runtime(tmp_path)
    ctx: dict = {}
    asyncio.run(rt.execute([
        {"extract_code_block": {
            "from": "```python\nx = 1\n```\n```python\ny = 2\n```",
            "into": "code",
            "language": "python",
        }}
    ], ctx))
    assert ctx["code"] == "x = 1"


def test_extract_code_block_language_filter(tmp_path: Path):
    """Skips non-matching language fences, returns the first
    matching one."""
    rt = _runtime(tmp_path)
    ctx: dict = {}
    asyncio.run(rt.execute([
        {"extract_code_block": {
            "from": "```bash\necho hello\n```\n```python\nprint('ok')\n```",
            "into": "code",
            "language": "python",
        }}
    ], ctx))
    assert ctx["code"] == "print('ok')"


def test_extract_code_block_no_fence_raises_by_default(tmp_path: Path):
    rt = _runtime(tmp_path)
    with pytest.raises(ScenarioError, match="no fenced"):
        asyncio.run(rt.execute([
            {"extract_code_block": {
                "from": "no fences here at all",
                "into": "code",
                "language": "python",
            }}
        ], {}))


def test_extract_code_block_passthrough_fallback(tmp_path: Path):
    rt = _runtime(tmp_path)
    ctx: dict = {}
    asyncio.run(rt.execute([
        {"extract_code_block": {
            "from": "no fences but still useful",
            "into": "code",
            "fallback": "passthrough",
        }}
    ], ctx))
    assert ctx["code"] == "no fences but still useful"


def test_extract_code_block_any_language_when_unspecified(tmp_path: Path):
    """language: '' (default) matches the first fence regardless of tag."""
    rt = _runtime(tmp_path)
    ctx: dict = {}
    asyncio.run(rt.execute([
        {"extract_code_block": {
            "from": "```\nbare fence\n```",
            "into": "code",
        }}
    ], ctx))
    assert ctx["code"] == "bare fence"


def test_extract_code_block_requires_from_and_into(tmp_path: Path):
    rt = _runtime(tmp_path)
    with pytest.raises(ScenarioError, match="requires 'from' and 'into'"):
        asyncio.run(rt.execute([
            {"extract_code_block": {"into": "x"}}
        ], {}))


def test_stop_when_pytest_passed_matches_on_green():
    """pytest_passed matches when failed==0, errors==0, passed>0."""
    ctx = {
        "test_result": {
            "ok": True,
            "output": {
                "passed": 4,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
            },
        },
    }
    match = _evaluate_stop_when([{"pytest_passed": "test_result"}], ctx)
    assert match is not None
    assert match.startswith("pytest_passed:test_result")
    assert "4 passed" in match


def test_stop_when_pytest_passed_no_match_on_failures():
    ctx = {
        "test_result": {
            "output": {"passed": 3, "failed": 1, "errors": 0},
        },
    }
    assert _evaluate_stop_when([{"pytest_passed": "test_result"}], ctx) is None


def test_stop_when_pytest_passed_no_match_on_zero_passed():
    """A run with 0 tests passed isn't green even if failed/errors are 0.
    Catches the corner case where the test file collected nothing."""
    ctx = {
        "test_result": {
            "output": {"passed": 0, "failed": 0, "errors": 0},
        },
    }
    assert _evaluate_stop_when([{"pytest_passed": "test_result"}], ctx) is None


def test_stop_when_pytest_passed_handles_missing_var():
    assert _evaluate_stop_when([{"pytest_passed": "absent"}], {}) is None


def test_stop_when_pytest_passed_handles_malformed_output():
    """A var that's not a dict, or output that's not a dict, doesn't crash."""
    assert _evaluate_stop_when(
        [{"pytest_passed": "x"}], {"x": "not a dict"},
    ) is None
    assert _evaluate_stop_when(
        [{"pytest_passed": "x"}], {"x": {"output": "not a dict"}},
    ) is None


def test_fizzbuzz_yaml_loads_and_validates():
    """Smoke: the canonical FizzBuzz scenario at config/scenarios/
    fizzbuzz.yaml parses and has the required-input contract we
    advertise. Catches accidental breakage during YAML edits."""
    # repo_root from this test file (tests/unit/X.py → repo root)
    repo_root = Path(__file__).resolve().parents[2]
    p = repo_root / "config" / "scenarios" / "fizzbuzz.yaml"
    assert p.exists(), f"FizzBuzz YAML missing at {p}"
    spec = load_scenario(p)
    assert spec.name == "fizzbuzz"
    assert "agent_id" in spec.required_inputs
    assert "target_dir" in spec.required_inputs
    assert spec.defaults.get("max_turns") == 10
    # The iterate step should be one of the top-level steps.
    iterate_steps = [s for s in spec.steps if "iterate" in s]
    assert len(iterate_steps) == 1


def test_fizzbuzz_yaml_input_validation():
    """Without required inputs, the runner refuses to execute the
    canonical scenario — proves the input contract is enforced."""
    from forest_soul_forge.daemon.scheduler.task_types.scenario import (
        scenario_runner,
    )
    repo_root = Path(__file__).resolve().parents[2]
    p = repo_root / "config" / "scenarios" / "fizzbuzz.yaml"
    out = asyncio.run(scenario_runner(
        {"scenario_path": str(p)},  # no inputs
        {"app": object(), "registry": object()},
    ))
    assert out["ok"] is False
    assert "missing required inputs" in out["error"]
    # Both required inputs flagged.
    assert "agent_id" in out["error"]
    assert "target_dir" in out["error"]
