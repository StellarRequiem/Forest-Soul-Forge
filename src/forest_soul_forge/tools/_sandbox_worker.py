"""Subprocess entrypoint for sandboxed tool dispatch — ADR-0051 T1.4.

Invoked as ``python -m forest_soul_forge.tools._sandbox_worker`` by
:class:`forest_soul_forge.tools.sandbox.MacOSSandboxExec` (and, when
T2 lands, by ``LinuxBwrap``). Runs INSIDE the OS sandbox; has only
the privileges the sandbox profile granted.

## Protocol

The parent process pickles a dict shaped:

    {
        "tool_module": "forest_soul_forge.tools.builtin.shell_exec",
        "tool_class":  "ShellExecTool",
        "args":        {...},
        "ctx":         <SerializableToolContext>,
    }

… and writes it to the worker's stdin. The worker reads stdin to
EOF, unpickles, runs the tool, and writes a pickled
:class:`SandboxResult` to stdout. Exit code is 0 on any in-band
result (success or tool_error); non-zero only when the worker
itself can't proceed (unexpected crash before pickling a result).

## Error mapping (worker side)

  - ``ImportError`` on tool_module       → ``error_kind="setup_failed"``
  - ``AttributeError`` on tool_class     → ``error_kind="setup_failed"``
  - ``TypeError`` constructing tool      → ``error_kind="setup_failed"``
  - ``ToolValidationError``              → ``error_kind="tool_error"``
                                            (matches in-process behavior)
  - ``ToolError`` or subclass            → ``error_kind="tool_error"``
  - Any other ``Exception``              → ``error_kind="unexpected"``
  - ``OSError`` resembling sandbox deny  → forwarded as
                                            ``error_kind="unexpected"``
                                            (the parent's stderr
                                            scanner upgrades it to
                                            sandbox_violation when the
                                            kernel logs the deny line)

## Why -I (isolated mode)

The parent invokes us as ``python -I``. That disables:
  - PYTHONPATH (we set an explicit one via env)
  - User site-packages
  - PYTHONSTARTUP scripts
  - Implicit current-dir prepend to sys.path

So a malicious agent can't smuggle code in via env-var tricks.
"""
from __future__ import annotations

import asyncio
import importlib
import io
import pickle
import sys
import traceback
from typing import Any

from forest_soul_forge.tools.sandbox import SandboxResult
from forest_soul_forge.tools.sandbox_context import SerializableToolContext


def _write_result(result: SandboxResult) -> None:
    """Pickle the result to stdout and flush.

    Uses :func:`sys.stdout.buffer` because stdout in -I mode is text-
    mode by default; we need raw bytes to round-trip the pickle.
    """
    data = pickle.dumps(result)
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except Exception:
        # If we can't even write stdout, there's nothing we can do.
        # The parent will see empty stdout + our exit code; it
        # classifies that as ``unexpected``.
        raise


def _read_payload() -> dict[str, Any]:
    """Read the pickled invocation from stdin (to EOF)."""
    raw = sys.stdin.buffer.read()
    if not raw:
        raise ValueError("worker stdin was empty — parent didn't pipe payload")
    return pickle.loads(raw)


def _run_tool_sync(tool: Any, args: dict[str, Any], ctx: Any) -> Any:
    """Run ``tool.execute`` whether it's async or sync.

    Tools in Forest are ``async def execute`` per the dispatcher's
    contract (``await tool.execute(args, ctx)`` at dispatcher.py
    line 773). We mirror that here. If a tool ever ships a sync
    execute (legacy or test fixture), :func:`asyncio.run` returning
    a non-coroutine will raise; we catch and wrap.
    """
    coro = tool.execute(args, ctx)
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


def main() -> int:
    """Entry point. Returns process exit code.

    Exit codes:
      0 — produced a valid SandboxResult on stdout (success OR
          in-band failure like tool_error)
      1 — couldn't even produce a result (corrupted payload, crash
          before pickling). Parent classifies as ``unexpected``.
    """
    # Step 1 — read + unpickle invocation payload.
    try:
        payload = _read_payload()
    except Exception as e:  # noqa: BLE001
        # No payload → no result possible. Print to stderr for the
        # parent's forensics, exit non-zero.
        print(
            f"[sandbox_worker] could not read/parse payload: {e}",
            file=sys.stderr,
        )
        return 1

    # Validate payload shape — anything unexpected becomes a clean
    # SandboxResult so the parent can audit-emit cleanly.
    if not isinstance(payload, dict):
        _write_result(SandboxResult(
            success=False,
            error_kind="setup_failed",
            stderr=f"worker payload is not a dict (got {type(payload).__name__})",
        ))
        return 0

    tool_module = payload.get("tool_module")
    tool_class = payload.get("tool_class")
    args = payload.get("args")
    ctx = payload.get("ctx")

    if not (isinstance(tool_module, str) and isinstance(tool_class, str)):
        _write_result(SandboxResult(
            success=False,
            error_kind="setup_failed",
            stderr=(
                f"worker payload missing tool_module/tool_class strings "
                f"(got {tool_module!r}, {tool_class!r})"
            ),
        ))
        return 0
    if not isinstance(args, dict):
        _write_result(SandboxResult(
            success=False,
            error_kind="setup_failed",
            stderr=f"worker payload args must be dict (got {type(args).__name__})",
        ))
        return 0
    if not isinstance(ctx, SerializableToolContext):
        _write_result(SandboxResult(
            success=False,
            error_kind="setup_failed",
            stderr=(
                f"worker payload ctx must be SerializableToolContext "
                f"(got {type(ctx).__name__})"
            ),
        ))
        return 0

    # Step 2 — import the tool class.
    try:
        mod = importlib.import_module(tool_module)
    except Exception as e:  # noqa: BLE001
        _write_result(SandboxResult(
            success=False,
            error_kind="setup_failed",
            stderr=f"could not import {tool_module!r}: {e}\n{traceback.format_exc()}",
        ))
        return 0

    try:
        cls = getattr(mod, tool_class)
    except AttributeError as e:
        _write_result(SandboxResult(
            success=False,
            error_kind="setup_failed",
            stderr=f"module {tool_module!r} has no attribute {tool_class!r}: {e}",
        ))
        return 0

    # Step 3 — instantiate. Tools in Forest are zero-arg constructible
    # by contract (the dispatcher constructs them at registration).
    try:
        tool = cls()
    except Exception as e:  # noqa: BLE001
        _write_result(SandboxResult(
            success=False,
            error_kind="setup_failed",
            stderr=f"could not instantiate {tool_class}(): {e}\n{traceback.format_exc()}",
        ))
        return 0

    # Step 4 — rehydrate the ToolContext.
    try:
        tool_ctx = ctx.to_tool_context()
    except Exception as e:  # noqa: BLE001
        _write_result(SandboxResult(
            success=False,
            error_kind="setup_failed",
            stderr=f"could not rehydrate ToolContext: {e}\n{traceback.format_exc()}",
        ))
        return 0

    # Step 5 — run the tool. Distinguish ToolError (in-band failure)
    # from any other exception (out-of-band crash) for the parent.
    try:
        # Import ToolError lazily — keeps worker startup cheap when
        # the tool itself never raises.
        from forest_soul_forge.tools.base import ToolError  # noqa: PLC0415

        try:
            tool_result = _run_tool_sync(tool, args, tool_ctx)
        except ToolError as te:
            # Tool-side reported failure (validation, etc.).
            # Pickle the exception so the parent / dispatcher can
            # rebuild it for audit emit.
            try:
                te_pickle = pickle.dumps(te)
            except Exception:
                # ToolError subclass not pickleable — fall back to
                # the string form.
                te_pickle = pickle.dumps(ToolError(str(te)))
            _write_result(SandboxResult(
                success=False,
                error_kind="tool_error",
                result_pickle=te_pickle,
                stderr=str(te),
            ))
            return 0

        # Success path — pickle the ToolResult.
        try:
            result_pickle = pickle.dumps(tool_result)
        except Exception as e:  # noqa: BLE001
            _write_result(SandboxResult(
                success=False,
                error_kind="unexpected",
                stderr=(
                    f"tool returned an un-pickleable result: {e}\n"
                    f"{traceback.format_exc()}"
                ),
            ))
            return 0

        _write_result(SandboxResult(
            success=True,
            result_pickle=result_pickle,
        ))
        return 0

    except Exception as e:  # noqa: BLE001
        # Any other exception — including ones the sandbox might have
        # caused (PermissionError from a deny on file write). The
        # parent's stderr scanner upgrades these to sandbox_violation
        # when the kernel-side deny line is also present.
        _write_result(SandboxResult(
            success=False,
            error_kind="unexpected",
            stderr=f"tool execution raised {type(e).__name__}: {e}\n{traceback.format_exc()}",
        ))
        return 0


if __name__ == "__main__":
    sys.exit(main())
