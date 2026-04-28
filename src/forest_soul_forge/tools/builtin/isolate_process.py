"""``isolate_process.v1`` — kill a PID via the sudo helper.

ADR-0033 Phase B2 — privileged. ResponseRogue's containment
primitive: given a PID and a reason, send SIGTERM (then SIGKILL
on a 2-second timer) via the ``/usr/local/sbin/fsf-priv`` helper
installed in Phase A6.

side_effects=external — this is the textbook "write-class"
operation. Per ADR-0033 A4, security_high gates every external
call regardless of tool config; security_mid gates filesystem
+ external (network passes for investigators). The tool also
sets ``requires_human_approval=True`` in its constraint so the
gate fires for non-security agents who somehow get this in
their kit too.

Refusals (raise ToolValidationError):
  * priv_client not wired (sudo helper not installed)
  * pid ≤ 1 (kernel/init)
  * pid validation already in PrivClient — but we duplicate the
    check here to refuse before any privileged call attempt
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_REASON = 256


class IsolateProcessTool:
    """SIGTERM / SIGKILL a process via the sudo helper.

    Args:
      pid    (int, required): PID to terminate. Must be > 1.
      reason (str, required): one-line explanation recorded in
        the audit chain via ToolResult.metadata so the operator
        can see why this PID was killed.

    Output:
      {
        "pid":          int,
        "reason":       str,
        "ok":           bool,
        "exit_code":    int,
        "stdout":       str,
        "stderr":       str
      }
    """

    name = "isolate_process"
    version = "1"
    side_effects = "external"

    def validate(self, args: dict[str, Any]) -> None:
        pid = args.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            raise ToolValidationError(
                f"pid must be a positive integer > 1; got {pid!r}"
            )
        reason = args.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ToolValidationError(
                "reason must be a non-empty string"
            )
        if len(reason) > _MAX_REASON:
            raise ToolValidationError(
                f"reason must be ≤ {_MAX_REASON} chars; got {len(reason)}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        if ctx.priv_client is None:
            raise ToolValidationError(
                "isolate_process.v1: no PrivClient wired on ctx (sudo "
                "helper not installed). Run docs/runbooks/sudo-helper-"
                "install.md to enable privileged ops, then restart "
                "the daemon."
            )
        from forest_soul_forge.security.priv_client import (
            PrivClientError,
            HelperMissing,
        )
        pid = args["pid"]
        reason = args["reason"]
        try:
            result = ctx.priv_client.kill_pid(pid)
        except HelperMissing as e:
            raise ToolValidationError(
                f"isolate_process.v1: helper missing — {e}"
            ) from e
        except PrivClientError as e:
            raise ToolValidationError(
                f"isolate_process.v1: client refused — {e}"
            ) from e

        return ToolResult(
            output={
                "pid":       pid,
                "reason":    reason,
                "ok":        result.ok,
                "exit_code": result.exit_code,
                "stdout":    result.stdout,
                "stderr":    result.stderr,
            },
            metadata={
                "priv_op":   "kill-pid",
                "priv_args": [str(pid)],
                "reason":    reason,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"isolate PID {pid}: {'OK' if result.ok else f'failed (exit {result.exit_code})'}"
            ),
        )
