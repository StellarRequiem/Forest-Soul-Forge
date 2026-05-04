"""Scenario runtime — YAML loader + step interpreter for ADR-0041 T4.

Lives in its own module so the scenario_task type runner stays thin
and the runtime can be unit-tested without importing the daemon's
HTTP layer or the scheduler.

Key types:

- :class:`ScenarioSpec` — the loaded YAML structure (name, inputs,
  defaults, steps).
- :class:`ScenarioRuntime` — stateful executor; one instance per
  scenario tick.
- :class:`ScenarioError` — typed failure raised on validation,
  step-dispatch, or stop-condition errors. The runner catches it
  and returns ``{"ok": False, ...}``.

Step types implemented in this burst:

- ``read_file`` — reads a path's contents into a context variable.
- ``write_file`` — writes a literal or interpolated string to a path.
- ``dispatch_tool`` — calls one tool against an existing agent.
- ``iterate`` — runs a sub-step list up to N times with stop_when
  conditions.

Variable interpolation: ``${var}`` inside any string value pulls
from the current context dict. Nested keys via dot syntax
(``${result.output.response}``). Missing keys raise ScenarioError.

The runtime is intentionally minimal — this is the v0.4 set, not
the kitchen sink. New step types are one function plus an entry
in ``_STEP_DISPATCH`` below; that's the deliberate extension shape.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ScenarioError(Exception):
    """Validation or execution failure inside a scenario."""


# ---------------------------------------------------------------------------
# Spec loader
# ---------------------------------------------------------------------------

@dataclass
class ScenarioSpec:
    """Parsed scenario.yaml. Holds enough state to drive a runtime
    pass without re-reading the file.
    """

    name: str
    description: str
    required_inputs: list[str]
    optional_inputs: list[str]
    defaults: dict[str, Any]
    steps: list[dict[str, Any]]


def load_scenario(path: Path) -> ScenarioSpec:
    """Load + validate a scenario YAML file.

    Raises :class:`ScenarioError` for missing file, malformed YAML,
    or missing required top-level keys.
    """
    if not path.exists():
        raise ScenarioError(f"scenario file not found: {path}")
    try:
        import yaml as _yaml  # local — yaml is already a dep
        raw = _yaml.safe_load(path.read_text())
    except Exception as e:
        raise ScenarioError(f"YAML parse failed: {e}") from e
    if not isinstance(raw, dict):
        raise ScenarioError(
            f"scenario must be a YAML mapping at the top level, got {type(raw).__name__}"
        )
    name = raw.get("name")
    steps = raw.get("steps")
    if not name or not isinstance(name, str):
        raise ScenarioError("scenario missing required 'name' (string)")
    if not isinstance(steps, list) or not steps:
        raise ScenarioError("scenario missing required 'steps' (non-empty list)")
    inputs = raw.get("inputs") or {}
    required = list(inputs.get("required") or [])
    optional = list(inputs.get("optional") or [])
    defaults = dict(raw.get("defaults") or {})
    return ScenarioSpec(
        name=name,
        description=str(raw.get("description") or ""),
        required_inputs=required,
        optional_inputs=optional,
        defaults=defaults,
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Variable interpolation
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_dotted(ctx: dict[str, Any], dotted: str) -> Any:
    """Walk a dotted key path into a (possibly nested) dict.

    ``dotted="result.output.response"`` returns
    ``ctx["result"]["output"]["response"]``. Missing key → KeyError.
    """
    parts = dotted.split(".")
    cur: Any = ctx
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif hasattr(cur, p):
            cur = getattr(cur, p)
        else:
            raise KeyError(dotted)
    return cur


def interpolate(value: Any, ctx: dict[str, Any]) -> Any:
    """Recursively interpolate ``${var}`` references in a value tree.

    Strings: every ``${path}`` is replaced. If the entire string is
    a single ``${path}`` and the resolved value is non-string, the
    typed value passes through (so ``max_turns: "${max_turns}"``
    yields an int when max_turns is an int). Otherwise the result
    is stringified.

    Lists / dicts: recurse into their elements / values.

    Other types: pass through unchanged.

    Missing variables raise :class:`ScenarioError` rather than
    silently producing empty strings — that's the discipline; bad
    interpolations should fail fast.
    """
    if isinstance(value, str):
        # Whole-string single-var case (preserves typed value)
        m = _VAR_RE.fullmatch(value)
        if m is not None:
            try:
                return _resolve_dotted(ctx, m.group(1).strip())
            except KeyError as e:
                raise ScenarioError(
                    f"interpolation failed: ${{{m.group(1).strip()}}} not in context"
                ) from e
        # Multi-var or embedded case → string concat
        def _sub(match: re.Match) -> str:
            key = match.group(1).strip()
            try:
                got = _resolve_dotted(ctx, key)
            except KeyError as e:
                raise ScenarioError(
                    f"interpolation failed: ${{{key}}} not in context"
                ) from e
            return str(got)
        return _VAR_RE.sub(_sub, value)
    if isinstance(value, list):
        return [interpolate(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: interpolate(v, ctx) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# Stop-when conditions (for iterate)
# ---------------------------------------------------------------------------

def _evaluate_stop_when(
    conditions: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> str | None:
    """Walk a list of stop_when conditions. Returns the matched
    condition's name (string-form) on first match, None otherwise.

    Supported condition shapes:

    - ``var_truthy: <name>`` — stops when ctx[name] is truthy.
    - ``var_equals: {var: <name>, value: <x>}`` — stops on equality.
    - ``pytest_passed: <name>`` — stops when ctx[name] is a
      dispatch_tool result whose pytest_run output indicates a
      green run: ``output.passed > 0`` AND ``output.failed == 0``
      AND ``output.errors == 0``. Domain-specific, but the
      canonical "exit the coding loop" check that
      live-test-fizzbuzz.command does as a regex on `summary_line`.
      The structured check is more reliable.

    Unknown shapes raise ScenarioError so typos surface loudly
    rather than as silent never-stops.
    """
    for cond in conditions:
        if not isinstance(cond, dict) or len(cond) != 1:
            raise ScenarioError(
                f"stop_when entry must be single-key dict, got {cond!r}"
            )
        ((kind, body),) = cond.items()
        if kind == "var_truthy":
            name = str(body)
            try:
                got = _resolve_dotted(ctx, name)
            except KeyError:
                got = None
            if got:
                return f"var_truthy:{name}"
        elif kind == "var_equals":
            if not isinstance(body, dict) or "var" not in body or "value" not in body:
                raise ScenarioError(
                    f"var_equals expects {{var: ..., value: ...}}, got {body!r}"
                )
            name = str(body["var"])
            target = body["value"]
            try:
                got = _resolve_dotted(ctx, name)
            except KeyError:
                got = None
            if got == target:
                return f"var_equals:{name}={target!r}"
        elif kind == "pytest_passed":
            name = str(body)
            try:
                got = _resolve_dotted(ctx, name)
            except KeyError:
                got = None
            # Expected shape from dispatch_tool result + pytest_run output.
            if not isinstance(got, dict):
                continue
            output = got.get("output") if isinstance(got, dict) else None
            if not isinstance(output, dict):
                continue
            try:
                passed = int(output.get("passed", 0))
                failed = int(output.get("failed", 0))
                errors = int(output.get("errors", 0))
            except (TypeError, ValueError):
                continue
            if passed > 0 and failed == 0 and errors == 0:
                return f"pytest_passed:{name}({passed} passed)"
        else:
            raise ScenarioError(f"unknown stop_when kind: {kind!r}")
    return None


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

@dataclass
class ScenarioRuntime:
    """Stateful executor for one scenario tick.

    ``app`` and ``registry`` are needed by ``dispatch_tool`` to reach
    the cached :class:`~forest_soul_forge.tools.dispatcher.ToolDispatcher`
    and look up agents. ``base_dir`` anchors relative paths in
    ``read_file`` / ``write_file`` (so config-relative paths stay
    config-relative). ``scenario_name`` and ``started_at`` flow into
    the daily-rotating ``session_id`` for dispatch_tool calls.
    """

    app: Any
    registry: Any
    base_dir: Path
    scenario_name: str
    started_at: datetime
    steps_executed: int = field(default=0, init=False)

    async def execute(
        self,
        steps: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a step list. Returns a small status dict the caller
        can fold into its outcome.
        """
        for raw_step in steps:
            if not isinstance(raw_step, dict) or len(raw_step) != 1:
                raise ScenarioError(
                    f"each step must be a single-key mapping, got {raw_step!r}"
                )
            ((kind, body),) = raw_step.items()
            handler = _STEP_DISPATCH.get(kind)
            if handler is None:
                raise ScenarioError(f"unknown step type: {kind!r}")
            interpolated_body = interpolate(body, ctx) if kind != "iterate" else body
            await handler(self, interpolated_body, ctx)
            self.steps_executed += 1
        return {"exit_reason": "completed"}


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------

async def _step_read_file(
    runtime: ScenarioRuntime,
    body: dict[str, Any],
    ctx: dict[str, Any],
) -> None:
    if not isinstance(body, dict):
        raise ScenarioError(f"read_file body must be a mapping, got {body!r}")
    path_str = body.get("path")
    into = body.get("into")
    if not path_str or not into:
        raise ScenarioError("read_file requires 'path' and 'into'")
    path = Path(path_str)
    if not path.is_absolute():
        path = runtime.base_dir / path
    try:
        ctx[str(into)] = path.read_text()
    except Exception as e:
        raise ScenarioError(f"read_file({path}) failed: {e}") from e


async def _step_write_file(
    runtime: ScenarioRuntime,
    body: dict[str, Any],
    ctx: dict[str, Any],
) -> None:
    if not isinstance(body, dict):
        raise ScenarioError(f"write_file body must be a mapping, got {body!r}")
    path_str = body.get("path")
    content = body.get("content")
    if not path_str or content is None:
        raise ScenarioError("write_file requires 'path' and 'content'")
    path = Path(path_str)
    if not path.is_absolute():
        path = runtime.base_dir / path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(str(content))
    except Exception as e:
        raise ScenarioError(f"write_file({path}) failed: {e}") from e


async def _step_dispatch_tool(
    runtime: ScenarioRuntime,
    body: dict[str, Any],
    ctx: dict[str, Any],
) -> None:
    if not isinstance(body, dict):
        raise ScenarioError(f"dispatch_tool body must be a mapping, got {body!r}")
    agent_id = body.get("agent_id")
    tool = body.get("tool")
    version = body.get("version", "1")
    args = dict(body.get("args") or {})
    into = body.get("into")  # optional — capture result if present
    if not agent_id or not tool:
        raise ScenarioError("dispatch_tool requires 'agent_id' and 'tool'")

    # Look up the agent.
    try:
        agent = runtime.registry.get_agent(str(agent_id))
    except Exception as e:
        raise ScenarioError(
            f"agent {agent_id!r} not found: {type(e).__name__}: {e}"
        ) from e

    # Get/build the dispatcher (same path tool_call_runner uses).
    from forest_soul_forge.daemon.deps import (
        ToolDispatcherUnavailable,
        build_or_get_tool_dispatcher,
    )
    try:
        dispatcher = build_or_get_tool_dispatcher(runtime.app)
    except ToolDispatcherUnavailable as e:
        raise ScenarioError(f"dispatcher unavailable: {e}") from e

    # Daily-rotating session_id, scenario-scoped.
    today_utc = runtime.started_at.strftime("%Y%m%d")
    session_id = (
        f"sched-scenario-{runtime.scenario_name}-{today_utc}"
    )

    provider = getattr(runtime.app.state, "active_provider", None)
    write_lock = getattr(runtime.app.state, "write_lock", None)
    if write_lock is None:
        raise ScenarioError("scheduler context missing write_lock")

    from forest_soul_forge.tools.dispatcher import (
        DispatchFailed,
        DispatchPendingApproval,
        DispatchRefused,
        DispatchSucceeded,
    )

    constitution_path = Path(agent.constitution_path)
    with write_lock:
        outcome = await dispatcher.dispatch(
            instance_id=agent.instance_id,
            agent_dna=agent.dna,
            role=agent.role,
            genre=None,
            session_id=session_id,
            constitution_path=constitution_path,
            tool_name=str(tool),
            tool_version=str(version),
            args=args,
            provider=provider,
        )

    if isinstance(outcome, DispatchSucceeded):
        if into:
            ctx[str(into)] = {
                "ok": True,
                "output": outcome.result.output,
                "tokens_used": outcome.result.tokens_used,
                "result_digest": outcome.result.result_digest,
            }
        return
    if isinstance(outcome, DispatchRefused):
        raise ScenarioError(
            f"dispatch refused: {outcome.reason} ({outcome.detail})"
        )
    if isinstance(outcome, DispatchPendingApproval):
        raise ScenarioError(
            f"tool {tool!r} requires human approval — scenarios may only "
            "use read_only-class tools"
        )
    if isinstance(outcome, DispatchFailed):
        raise ScenarioError(f"dispatch failed: {outcome.reason}")
    raise ScenarioError(f"unknown dispatch outcome: {type(outcome).__name__}")


_FENCE_RE = re.compile(
    # Match ``` followed by optional language tag, then capture the
    # body up to the next closing fence. DOTALL so .* spans newlines.
    r"```(?P<lang>[a-zA-Z0-9_+-]*)\s*\n(?P<body>.*?)\n?```",
    re.DOTALL,
)


async def _step_extract_code_block(
    runtime: ScenarioRuntime,
    body: dict[str, Any],
    ctx: dict[str, Any],
) -> None:
    """Extract a fenced code block from a string variable.

    Body shape::

        extract_code_block:
          from: "${llm_result.output.response}"
          into: code
          language: python   # optional; default "" matches any
          fallback: passthrough  # optional; default "raise"

    The extractor finds the first ```<language> ... ``` block in
    ``from`` and stores its inner text in ``into``. If no fence
    matches and ``fallback: passthrough`` is set, the entire input
    is stored verbatim — useful for LLMs that don't always wrap
    code in fences. Default ``fallback: raise`` aborts the
    scenario with ScenarioError.

    This is the load-bearing extractor for coding-loop scenarios:
    LLMs return Python wrapped in ```python ... ``` and we need
    just the body to write back to fizzbuzz.py.
    """
    if not isinstance(body, dict):
        raise ScenarioError(f"extract_code_block body must be a mapping, got {body!r}")
    src = body.get("from")
    into = body.get("into")
    language = body.get("language", "")
    fallback = body.get("fallback", "raise")
    if src is None or not into:
        raise ScenarioError("extract_code_block requires 'from' and 'into'")
    text = str(src)
    for match in _FENCE_RE.finditer(text):
        if not language or match.group("lang") == language:
            ctx[str(into)] = match.group("body")
            return
    if fallback == "passthrough":
        ctx[str(into)] = text
        return
    raise ScenarioError(
        f"extract_code_block: no fenced "
        f"{'```' + language if language else 'code'} block found in 'from'"
    )


async def _step_iterate(
    runtime: ScenarioRuntime,
    body: dict[str, Any],
    ctx: dict[str, Any],
) -> None:
    """Loop a sub-step list up to ``max_turns`` times.

    Stops early when any ``stop_when`` condition matches against the
    context after a turn completes. Sub-steps interpolate against
    the SAME context as their parent — variables set inside iterate
    survive the loop and are visible to subsequent steps.
    """
    if not isinstance(body, dict):
        raise ScenarioError(f"iterate body must be a mapping, got {body!r}")
    max_turns_raw = body.get("max_turns")
    sub_steps = body.get("step")
    stop_when = body.get("stop_when") or []
    if max_turns_raw is None or sub_steps is None:
        raise ScenarioError("iterate requires 'max_turns' and 'step'")
    if not isinstance(sub_steps, list):
        raise ScenarioError("iterate.step must be a list")
    # max_turns can be a string ${var} that interpolates to an int;
    # interpolate now (after the parent skipped it) so we get the typed value.
    interpolated_max = interpolate(max_turns_raw, ctx)
    try:
        max_turns = int(interpolated_max)
    except (TypeError, ValueError) as e:
        raise ScenarioError(
            f"iterate.max_turns must be an int, got {interpolated_max!r}"
        ) from e

    for turn in range(max_turns):
        ctx["_iterate_turn"] = turn
        # Run each sub-step (each gets its own interpolation pass
        # via the recursive execute call).
        for raw_step in sub_steps:
            if not isinstance(raw_step, dict) or len(raw_step) != 1:
                raise ScenarioError(
                    f"each iterate sub-step must be a single-key mapping, got {raw_step!r}"
                )
            ((kind, sub_body),) = raw_step.items()
            handler = _STEP_DISPATCH.get(kind)
            if handler is None:
                raise ScenarioError(f"unknown step type inside iterate: {kind!r}")
            interpolated_body = (
                interpolate(sub_body, ctx) if kind != "iterate" else sub_body
            )
            await handler(runtime, interpolated_body, ctx)
            runtime.steps_executed += 1
        # Stop check after the turn.
        match = _evaluate_stop_when(stop_when, ctx)
        if match is not None:
            ctx["_iterate_exit_reason"] = match
            return
    ctx["_iterate_exit_reason"] = f"max_turns:{max_turns}"


_STEP_DISPATCH: dict[str, Any] = {
    "read_file": _step_read_file,
    "write_file": _step_write_file,
    "dispatch_tool": _step_dispatch_tool,
    "extract_code_block": _step_extract_code_block,
    "iterate": _step_iterate,
}
