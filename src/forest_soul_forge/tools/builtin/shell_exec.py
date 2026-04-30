"""``shell_exec.v1`` — run a shell command via argv list (NOT shell=True).

Side effects: ``external``. The heaviest of the SW-track tools — at
the actuator-genre tier this triggers ``requires_human_approval=true``
on every call, so an Engineer agent's shell_exec dispatches always
land in the operator queue first.

Per-agent constitution must populate (under shell_exec.v1's
constraints block):
  allowed_commands: [git, pytest, ls, cat, ...]   # the argv[0] allowlist
  allowed_paths:    [/abs/path/to/repo, ...]      # cwd + working tree

Critical safety properties:

  - argv MUST be a list of strings — no shell metacharacter expansion,
    no `bash -c "..."`, no glob expansion. Operators who want a
    glob expand it themselves before passing argv.
  - argv[0] must be in the agent's allowed_commands. Bare command
    name (e.g. "git"), not absolute path — we look up via $PATH and
    refuse if PATH resolution gives nothing. Absolute paths in argv[0]
    are explicitly refused (they'd let an agent run /tmp/malware after
    only allowing "git").
  - cwd, when supplied, must resolve to a path inside allowed_paths.
    When omitted, defaults to the FIRST allowed_path (the canonical
    project root for this agent).
  - Subprocess timeout is mandatory (default 30s, max 300s). Hung
    commands don't tie up the dispatcher forever.
  - stdout + stderr are captured and truncated (100 KB stdout, 50 KB
    stderr) so a verbose tool doesn't blow the audit chain.
  - Environment is INHERITED from the daemon (we don't strip env vars
    per call) — operators should run the daemon under a restricted
    user account if env-leakage is a concern. Future v2 may add an
    env-allowlist constraint.

Future evolution:
  - v2: env-allowlist (constraints.allowed_env_vars; everything else
        stripped from the subprocess env)
  - v2: stdin support (some commands need piped input)
  - v2: streaming stdout (long-running commands like `pytest -v`
        emit progress; v1 just buffers everything until exit)
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)
from forest_soul_forge.tools.builtin.code_read import (
    _is_within_any,
    _resolve_allowlist,
)

DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S    = 300
MIN_TIMEOUT_S    = 1
STDOUT_TRUNCATE_BYTES = 100_000   # 100 KB
STDERR_TRUNCATE_BYTES = 50_000    # 50 KB


class ShellExecError(ToolValidationError):
    """Raised by shell_exec for argv-shape, allowlist, or run failures."""


class ShellExecTool:
    """Args:
      argv (list[str], required): command + arguments. argv[0] must
        be a bare command name (no slashes — no absolute paths) and
        must be in the agent's allowed_commands. ALL elements must be
        strings — we reject lists/dicts/numbers to make the no-shell
        contract obvious to the model emitting the call.
      cwd (str, optional): working directory. Must be inside
        allowed_paths. Defaults to the first allowed_path.
      timeout_s (int, optional): subprocess timeout in seconds.
        Default 30, max 300, min 1.

    Output:
      {
        "argv":       list[str],   # what was actually run
        "cwd":        str,          # the resolved cwd
        "returncode": int,          # the subprocess exit code
        "stdout":     str,          # captured stdout (utf-8, truncated)
        "stderr":     str,          # captured stderr (utf-8, truncated)
        "elapsed_s":  float,        # wall-clock time spent in subprocess
        "timed_out":  bool,         # true if the subprocess hit timeout_s
        "stdout_truncated": bool,
        "stderr_truncated": bool,
      }

    Constraints (read from ctx.constraints):
      allowed_commands: list[str]   # required, bare command names
      allowed_paths:    list[str]   # required, absolute paths
    """

    name = "shell_exec"
    version = "1"
    side_effects = "external"

    def validate(self, args: dict[str, Any]) -> None:
        argv = args.get("argv")
        if not isinstance(argv, list) or len(argv) == 0:
            raise ToolValidationError(
                f"argv must be a non-empty list of strings; got {type(argv).__name__}"
            )
        for i, el in enumerate(argv):
            if not isinstance(el, str):
                raise ToolValidationError(
                    f"argv[{i}] must be a string; got {type(el).__name__} "
                    f"(no shell expansion: pass each token as a separate string)"
                )
        if "/" in argv[0] or "\\" in argv[0]:
            raise ToolValidationError(
                f"argv[0] {argv[0]!r} contains a path separator — only bare "
                f"command names are accepted (PATH lookup is the gate)"
            )
        if argv[0].startswith("-"):
            raise ToolValidationError(
                f"argv[0] {argv[0]!r} starts with '-' — first element must be "
                f"a command name, not a flag"
            )

        cwd = args.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
            raise ToolValidationError("cwd must be a non-empty string when provided")

        timeout_s = args.get("timeout_s", DEFAULT_TIMEOUT_S)
        if not isinstance(timeout_s, int) or timeout_s < MIN_TIMEOUT_S or timeout_s > MAX_TIMEOUT_S:
            raise ToolValidationError(
                f"timeout_s must be in [{MIN_TIMEOUT_S}, {MAX_TIMEOUT_S}]; got {timeout_s!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        argv: list[str] = list(args["argv"])
        timeout_s: int = int(args.get("timeout_s", DEFAULT_TIMEOUT_S))
        cwd_arg = args.get("cwd")

        # Allowed-commands check.
        allowed_commands_raw = ctx.constraints.get("allowed_commands") or ()
        if not allowed_commands_raw:
            raise ShellExecError(
                "agent has no allowed_commands in its constitution — "
                "shell_exec.v1 refuses to run any command"
            )
        allowed_commands = tuple(
            str(c) for c in allowed_commands_raw if isinstance(c, str)
        )
        if argv[0] not in allowed_commands:
            raise ShellExecError(
                f"command {argv[0]!r} is not in the agent's allowed_commands "
                f"({sorted(allowed_commands)})"
            )

        # PATH lookup. shutil.which respects $PATH the same way
        # subprocess does, so this matches what the actual exec will see.
        resolved_argv0 = shutil.which(argv[0])
        if resolved_argv0 is None:
            raise ShellExecError(
                f"command {argv[0]!r} is in allowed_commands but not on $PATH; "
                f"either install it or add the install directory to PATH"
            )

        # Allowed-paths + cwd resolution.
        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise ShellExecError(
                "agent has no allowed_paths in its constitution — "
                "shell_exec.v1 needs a working-directory allowlist"
            )
        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))
        if not allowed_roots:
            raise ShellExecError(
                "allowed_paths contained no valid entries"
            )

        if cwd_arg is None:
            cwd_resolved = allowed_roots[0]
        else:
            try:
                cwd_resolved = Path(cwd_arg).resolve(strict=True)
            except FileNotFoundError:
                raise ShellExecError(f"cwd does not exist: {cwd_arg!r}")
            except OSError as e:
                raise ShellExecError(f"cwd resolution failed: {e}") from e
            if not cwd_resolved.is_dir():
                raise ShellExecError(f"cwd is not a directory: {cwd_resolved}")
            if not _is_within_any(cwd_resolved, allowed_roots):
                raise ShellExecError(
                    f"cwd {str(cwd_resolved)!r} is outside the agent's allowed_paths "
                    f"({[str(p) for p in allowed_roots]})"
                )

        # Run. We swap argv[0] for the resolved path so the subprocess
        # gets the same binary regardless of cwd-relative PATH quirks.
        actual_argv = [resolved_argv0] + argv[1:]
        timed_out = False
        try:
            proc = subprocess.run(
                actual_argv,
                cwd=str(cwd_resolved),
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            returncode = proc.returncode
            stdout_bytes = proc.stdout
            stderr_bytes = proc.stderr
        except subprocess.TimeoutExpired as e:
            timed_out = True
            returncode = -1
            stdout_bytes = e.stdout or b""
            stderr_bytes = e.stderr or b""
        except OSError as e:
            raise ShellExecError(f"subprocess launch failed: {e}") from e

        stdout_full_len = len(stdout_bytes)
        stderr_full_len = len(stderr_bytes)
        stdout_truncated = stdout_full_len > STDOUT_TRUNCATE_BYTES
        stderr_truncated = stderr_full_len > STDERR_TRUNCATE_BYTES

        if stdout_truncated:
            stdout_bytes = stdout_bytes[:STDOUT_TRUNCATE_BYTES]
        if stderr_truncated:
            stderr_bytes = stderr_bytes[:STDERR_TRUNCATE_BYTES]

        # Decode permissively — replace bad bytes rather than crashing
        # on non-utf-8 output. Real-world tools occasionally emit raw
        # bytes (binary diffs, terminal escapes); we prioritize
        # not-crashing the audit pipeline.
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        return ToolResult(
            output={
                "argv":              argv,
                "cwd":               str(cwd_resolved),
                "returncode":        returncode,
                "stdout":            stdout,
                "stderr":            stderr,
                "timed_out":         timed_out,
                "stdout_truncated":  stdout_truncated,
                "stderr_truncated":  stderr_truncated,
            },
            metadata={
                "resolved_argv0":    resolved_argv0,
                "stdout_full_len":   stdout_full_len,
                "stderr_full_len":   stderr_full_len,
                "allowed_commands":  list(allowed_commands),
                "allowed_roots":     [str(p) for p in allowed_roots],
            },
            side_effect_summary=(
                f"shell_exec: {argv[0]} → rc={returncode} "
                f"stdout={stdout_full_len}b stderr={stderr_full_len}b"
                f"{' (timed out)' if timed_out else ''}"
            ),
        )
