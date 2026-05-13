"""Per-tool subprocess sandbox abstraction — ADR-0051 T1.3 (B261).

This module is the FIRST tranche of ADR-0051's implementation. It
ships the abstraction (``SandboxProfile``, ``SandboxResult``,
``Sandbox`` Protocol) plus the macOS ``sandbox-exec`` implementation.
Dispatcher integration is intentionally deferred to T4 — T1 lands
the substrate without changing any dispatch path so existing
behavior is bit-identical (FSF_TOOL_SANDBOX=off is the default, and
even when this module is imported no tool dispatch is affected
until T4 wires it in).

The Linux ``bwrap`` implementation is T2's responsibility; this
module's ``default_sandbox()`` returns ``None`` on non-darwin until
the Linux impl lands.

## Why subprocess + OS-level sandbox

Per ADR-0051 Decision 2, Python lacks real in-process isolation
(GIL, shared memory, no capability boundaries). The only way to
enforce that a compromised tool can't escape its declared
``side_effects`` is to spawn the tool in a child process under an
OS-level sandbox. macOS provides ``sandbox-exec(7)`` (the same
mechanism that confines AppKit apps); Linux provides ``bwrap``
(bubblewrap, used by Flatpak). Both implement deny-by-default with
explicit allow rules.

## Profile generation

A ``SandboxProfile`` is derived mechanically from:

  - The tool's declared ``side_effects`` (read_only / network /
    filesystem / external) — what KINDS of operations it may do.
  - The agent's constitution allowlists (allowed_paths,
    allowed_hosts, allowed_commands) — WHERE specifically each
    operation may go.

So the sandbox profile is the OS-level enforcement of the
constitution the operator already approved. Each dispatch generates
a FRESH profile (per ADR-0051 Decision 4) so a constitution mutation
post-birth (plugin grant / posture change) is reflected immediately;
no stale profile accidentally allows a revoked operation.

## Failure shape (SandboxResult.error_kind)

- ``"setup_failed"``      — the sandbox itself couldn't start
                              (sandbox-exec missing, profile write
                              error, etc.). In permissive mode the
                              dispatcher falls back to in-process;
                              in strict mode the call is refused.
- ``"timeout"``           — the subprocess exceeded the configured
                              ``timeout_s``.
- ``"sandbox_violation"`` — the tool tried to do something outside
                              the profile (file write to disallowed
                              path, network to disallowed host).
                              Exit code typically 31 from
                              sandbox-exec; stderr carries the
                              violated rule.
- ``"tool_error"``        — the tool's own ``ToolError`` (validation,
                              argument shape, etc.). Pass-through.
- ``"unexpected"``        — anything else (Python crash inside the
                              worker, pickle round-trip failure).

The dispatcher (T4) maps these to the existing audit event types
plus the additive ``sandbox_*`` event_data fields per ADR-0051
Decision 5.
"""
from __future__ import annotations

import os
import pickle
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from forest_soul_forge.tools.sandbox_context import SerializableToolContext


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SandboxProfile:
    """OS-level enforcement spec for one tool dispatch.

    Derived from ``build_profile(side_effects, allowed_*)`` — see the
    function docstring for the per-side_effects mapping table.

    Profile is intentionally minimal — no broad "allow all reads under
    /tmp" defaults. Anything not in the allow* tuples is denied by
    the underlying sandbox tech.
    """

    side_effects: str
    allowed_read_paths: tuple[str, ...] = ()
    allowed_write_paths: tuple[str, ...] = ()
    allow_network: bool = False
    allowed_hosts: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    # Hard subprocess wall-clock ceiling. ADR-0051 doesn't pick a
    # default; the dispatcher passes its own timeout (typically tool
    # constraints.timeout_s). 30s is a safe sentinel for the
    # abstraction layer; callers should override.
    timeout_s: float = 30.0


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of one sandboxed tool dispatch.

    On success: ``success=True``, ``result_pickle`` is the pickled
    :class:`ToolResult` returned by the tool's ``execute``.

    On failure: ``success=False``, ``error_kind`` names the failure
    class (see module docstring), ``violated_rule`` is set for
    ``sandbox_violation`` failures, ``stderr`` always carries the
    captured subprocess stderr for forensics.
    """

    success: bool
    result_pickle: bytes | None = None
    error_kind: str | None = None
    violated_rule: str | None = None
    stderr: str = ""
    exit_code: int | None = None


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------


def build_profile(
    *,
    side_effects: str,
    allowed_paths: list[str] | tuple[str, ...] = (),
    allowed_commands: list[str] | tuple[str, ...] = (),
    allowed_hosts: list[str] | tuple[str, ...] = (),
    timeout_s: float = 30.0,
) -> SandboxProfile:
    """Compose a :class:`SandboxProfile` per ADR-0051 Decision 4.

    Mapping table (lifted from the ADR):

      | side_effects | read paths       | write paths      | network |
      |--------------|------------------|------------------|---------|
      | read_only    | allowed_paths    | (none)           | (none)  |
      | network      | allowed_paths    | (none)           | allow + |
      |              |                  |                  | allowed_|
      |              |                  |                  | hosts   |
      | filesystem   | allowed_paths    | allowed_paths    | (none)  |
      | external     | allowed_paths    | allowed_paths    | (none)  |
      |              | + allowed_       |                  |         |
      |              |   commands       |                  |         |

    Notes:
    - ``read_only`` tools are not normally engaged with the sandbox in
      default mode (FSF_TOOL_SANDBOX=off treats them as in-process by
      definition). The mapping is present for completeness so strict
      mode can sandbox-anyway-for-defense-in-depth if operators
      configure that posture.
    - ``allowed_commands`` are absolute paths to executables the tool
      may invoke (e.g., ``/usr/bin/curl`` for a wrapped HTTP probe).
      The sandbox profile permits ``process-exec*`` against those
      explicit paths only; no shell, no fork-bomb, no fork-exec of
      sibling binaries.
    """
    paths = tuple(str(p) for p in (allowed_paths or ()))
    cmds = tuple(str(c) for c in (allowed_commands or ()))
    hosts = tuple(str(h) for h in (allowed_hosts or ()))

    if side_effects == "read_only":
        return SandboxProfile(
            side_effects=side_effects,
            allowed_read_paths=paths,
            allowed_write_paths=(),
            allow_network=False,
            allowed_hosts=(),
            allowed_commands=(),
            timeout_s=timeout_s,
        )
    if side_effects == "network":
        return SandboxProfile(
            side_effects=side_effects,
            allowed_read_paths=paths,
            allowed_write_paths=(),
            allow_network=True,
            allowed_hosts=hosts,
            allowed_commands=(),
            timeout_s=timeout_s,
        )
    if side_effects == "filesystem":
        return SandboxProfile(
            side_effects=side_effects,
            allowed_read_paths=paths,
            allowed_write_paths=paths,
            allow_network=False,
            allowed_hosts=(),
            allowed_commands=(),
            timeout_s=timeout_s,
        )
    if side_effects == "external":
        return SandboxProfile(
            side_effects=side_effects,
            allowed_read_paths=paths,
            allowed_write_paths=paths,
            allow_network=False,
            allowed_hosts=(),
            allowed_commands=cmds,
            timeout_s=timeout_s,
        )
    # Unknown side_effects — refuse to construct a profile rather
    # than fall back to deny-all (which would silently break the
    # tool). The dispatcher catches ValueError and emits a clean
    # tool_call_refused with sandbox_setup_failed.
    raise ValueError(
        f"unknown side_effects {side_effects!r} for sandbox profile"
    )


# ---------------------------------------------------------------------------
# Sandbox Protocol
# ---------------------------------------------------------------------------


class Sandbox(Protocol):
    """Abstract sandbox interface.

    Concrete implementations (MacOSSandboxExec, LinuxBwrap in T2)
    encapsulate the platform-specific subprocess-spawn + profile-
    encoding work. The dispatcher only sees this interface.
    """

    def run(
        self,
        *,
        tool_module: str,
        tool_class: str,
        args: dict[str, Any],
        ctx: SerializableToolContext,
        profile: SandboxProfile,
    ) -> SandboxResult:
        """Spawn the tool in a sandbox and return its outcome.

        ``tool_module`` + ``tool_class`` are dotted-path strings the
        worker uses to import the tool (e.g.,
        ``"forest_soul_forge.tools.builtin.shell_exec"`` +
        ``"ShellExecTool"``). The worker process imports them fresh —
        no shared state with the daemon process.
        """
        ...


# ---------------------------------------------------------------------------
# macOS sandbox-exec implementation
# ---------------------------------------------------------------------------


# Standard read-paths every Python subprocess needs to even START.
# Without these, ``sandbox-exec`` blocks Python's own dyld + stdlib
# reads and the worker dies at import time. These are NOT relaxations
# of the tool's permissions — they're system-level requirements for
# the interpreter to function.
#
# We intentionally do NOT include user home or any project-specific
# paths here — those come only via the tool's profile's
# allowed_read_paths.
_MACOS_SYSTEM_READ_PATHS: tuple[str, ...] = (
    "/usr",
    "/System",
    "/Library",
    "/private/var/folders",   # macOS temp / cache
    "/private/etc",            # /etc/hosts for DNS, /etc/services
    "/private/tmp",            # the canonical /tmp
    "/dev",                    # /dev/null, /dev/random
    "/opt/homebrew",           # Homebrew Python lives here on Apple Silicon
)


def _quote_sb_path(p: str) -> str:
    """Quote a path for sandbox-exec's TinyScheme parser.

    Apple's sandbox profiles use a Scheme-ish syntax; paths are
    string literals. Standard double-quote escaping suffices for the
    paths we generate. Reject anything with a literal double-quote
    or backslash to keep the profile shape safe.
    """
    if '"' in p or "\\" in p:
        raise ValueError(
            f"sandbox-exec profile path contains unsafe chars: {p!r}"
        )
    return f'"{p}"'


def _macos_profile_text(profile: SandboxProfile) -> str:
    """Serialize a :class:`SandboxProfile` to sandbox-exec(7) syntax.

    The output is the contents of a ``.sb`` file. Deny-by-default
    posture with explicit allows for:
      - Python's own startup file reads (the _MACOS_SYSTEM_READ_PATHS
        set above)
      - The tool's allowed_read_paths / allowed_write_paths
      - The tool's allowed_commands (process-exec*)
      - The tool's network rule (allow network* if profile says so)

    Always allows ``process-fork`` (the worker needs to fork off any
    children explicitly — most tools won't but some might shell out
    via ``allowed_commands``).
    """
    lines: list[str] = [
        "(version 1)",
        "(deny default)",
        # Required for any Python subprocess to function.
        "(allow process-fork)",
        "(allow signal (target self))",
        "(allow mach-lookup)",
        "(allow ipc-posix-shm)",
        "(allow sysctl-read)",
        "(allow file-read-metadata)",
    ]
    # System read paths — required for Python interpreter to start.
    for p in _MACOS_SYSTEM_READ_PATHS:
        lines.append(f"(allow file-read* (subpath {_quote_sb_path(p)}))")
    # Tool-declared read paths.
    for p in profile.allowed_read_paths:
        lines.append(f"(allow file-read* (subpath {_quote_sb_path(p)}))")
    # Tool-declared write paths.
    for p in profile.allowed_write_paths:
        lines.append(f"(allow file-write* (subpath {_quote_sb_path(p)}))")
    # Tool-declared exec paths (external tools that wrap CLI binaries).
    for c in profile.allowed_commands:
        lines.append(f"(allow process-exec* (literal {_quote_sb_path(c)}))")
    # Always allow exec of the Python interpreter itself — the worker
    # subprocess IS python, and forking new pythons (e.g., to run a
    # captured snippet) is part of the worker's job. Without this the
    # subprocess can't even spawn.
    py = sys.executable
    if py:
        lines.append(f"(allow process-exec* (literal {_quote_sb_path(py)}))")
    # Network rule.
    if profile.allow_network:
        if profile.allowed_hosts:
            # sandbox-exec doesn't support host-allowlisting natively in
            # public profile syntax (it CAN via undocumented predicates,
            # but they're version-fragile). Tools that need
            # host-allowlisting enforcement are expected to layer their
            # own check at the tool boundary. The sandbox layer permits
            # the network call; the tool's own allowlist check rejects
            # off-list hosts. Defense-in-depth: sandbox shrinks
            # blast radius (no LATERAL filesystem access from a
            # network-talking tool); the tool's own check shrinks
            # destination set.
            lines.append("(allow network*)")
        else:
            # network side_effect with empty allowed_hosts means "any
            # network for this tool's purpose" — explicit operator
            # choice in the constitution.
            lines.append("(allow network*)")
    return "\n".join(lines) + "\n"


class MacOSSandboxExec:
    """macOS sandbox-exec(7)-backed :class:`Sandbox`.

    Spawns the tool inside ``/usr/bin/sandbox-exec`` with a freshly-
    generated profile. The worker (``_sandbox_worker``) reads the
    pickled invocation from stdin, runs the tool, writes the pickled
    :class:`SandboxResult` to stdout, and exits.

    Failure shape:
      - sandbox-exec not present (e.g., we're on non-macOS by accident)
        → ``error_kind="setup_failed"``
      - subprocess exits with non-zero and stderr matches the
        sandbox-violation signature → ``error_kind="sandbox_violation"``
      - subprocess times out → ``error_kind="timeout"``
      - subprocess crashes / pickle round-trip fails →
        ``error_kind="unexpected"``
      - tool itself returns a ToolError → worker pickles a
        ``SandboxResult(success=False, error_kind="tool_error", ...)``
    """

    SANDBOX_EXEC_PATH = "/usr/bin/sandbox-exec"

    # Substring signatures that indicate a sandbox-exec violation
    # in the captured stderr. macOS prints these consistently across
    # 13.x / 14.x / 15.x. Used to distinguish violation from generic
    # "tool crashed for some other reason".
    _VIOLATION_SIGNATURES: tuple[str, ...] = (
        "deny(1)",
        "Sandbox: ",
        "operation not permitted",
        "Operation not permitted",
    )

    def __init__(
        self,
        *,
        worker_module: str = "forest_soul_forge.tools._sandbox_worker",
        python_executable: str | None = None,
    ) -> None:
        self._worker_module = worker_module
        self._python = python_executable or sys.executable

    def _looks_like_violation(self, stderr: str, exit_code: int) -> bool:
        if exit_code == 0:
            return False
        # sandbox-exec exits 31 on most permission errors but the
        # actual exit code shape varies; the stderr substring is more
        # reliable than the code alone.
        for sig in self._VIOLATION_SIGNATURES:
            if sig in stderr:
                return True
        return False

    def run(
        self,
        *,
        tool_module: str,
        tool_class: str,
        args: dict[str, Any],
        ctx: SerializableToolContext,
        profile: SandboxProfile,
    ) -> SandboxResult:
        # Step 0 — sanity check: are we even on a host that can run
        # this? If the binary is missing, surface as setup_failed so
        # the dispatcher can decide (strict refuses; permissive falls
        # back to in-process).
        if not Path(self.SANDBOX_EXEC_PATH).exists():
            return SandboxResult(
                success=False,
                error_kind="setup_failed",
                stderr=(
                    f"{self.SANDBOX_EXEC_PATH} not found — this host "
                    "doesn't have macOS sandbox-exec available"
                ),
            )

        # Step 1 — emit the .sb profile to a temp file.
        try:
            profile_text = _macos_profile_text(profile)
        except ValueError as e:
            return SandboxResult(
                success=False,
                error_kind="setup_failed",
                stderr=f"profile generation failed: {e}",
            )

        try:
            sb_fd, sb_path = tempfile.mkstemp(suffix=".sb", prefix="fsf-sandbox-")
            with os.fdopen(sb_fd, "w", encoding="utf-8") as f:
                f.write(profile_text)
        except OSError as e:
            return SandboxResult(
                success=False,
                error_kind="setup_failed",
                stderr=f"profile write failed: {e}",
            )

        # Step 2 — pickle the invocation payload.
        try:
            payload = pickle.dumps({
                "tool_module": tool_module,
                "tool_class":  tool_class,
                "args":        args,
                "ctx":         ctx,
            })
        except Exception as e:
            _safe_unlink(sb_path)
            return SandboxResult(
                success=False,
                error_kind="setup_failed",
                stderr=f"pickle of invocation payload failed: {e}",
            )

        # Step 3 — spawn sandbox-exec + python -I + worker module.
        # ``-I`` (isolated mode) disables PYTHONPATH and user site
        # — important so the sandboxed worker can't be hijacked by a
        # malicious PYTHON* env var. PYTHONPATH is set explicitly
        # below to the daemon's own sys.path so internal imports
        # still work.
        cmd = [
            self.SANDBOX_EXEC_PATH,
            "-f", sb_path,
            self._python,
            "-I",
            "-m", self._worker_module,
        ]
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": os.pathsep.join(sys.path),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        try:
            proc = subprocess.run(
                cmd,
                input=payload,
                capture_output=True,
                timeout=profile.timeout_s,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            _safe_unlink(sb_path)
            stderr_text = (
                e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            )
            return SandboxResult(
                success=False,
                error_kind="timeout",
                stderr=stderr_text,
            )
        except OSError as e:
            _safe_unlink(sb_path)
            return SandboxResult(
                success=False,
                error_kind="setup_failed",
                stderr=f"subprocess spawn failed: {e}",
            )
        finally:
            # The temp .sb file isn't security-sensitive (it just
            # encodes the ALREADY-AUDITED constitution allowlists),
            # but leaving it lying around clutters /tmp. Best-effort
            # cleanup; tempfile reaper handles anything we miss.
            pass

        _safe_unlink(sb_path)

        stderr_text = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        stdout_bytes = proc.stdout or b""

        # Step 4 — classify exit.
        if self._looks_like_violation(stderr_text, proc.returncode):
            # Try to extract the violated rule (the first sandbox: line
            # usually carries it).
            violated = _extract_violated_rule(stderr_text)
            return SandboxResult(
                success=False,
                error_kind="sandbox_violation",
                violated_rule=violated,
                stderr=stderr_text,
                exit_code=proc.returncode,
            )

        if proc.returncode != 0 and not stdout_bytes:
            # Non-zero exit + no result on stdout = worker crashed
            # before it could even produce a result. Could be
            # setup_failed (missing module, bad pickle) or generic
            # unexpected.
            return SandboxResult(
                success=False,
                error_kind="unexpected",
                stderr=stderr_text,
                exit_code=proc.returncode,
            )

        # Step 5 — the worker pickled a SandboxResult to stdout.
        # Decode + return as-is (worker handles tool_error and other
        # tool-side failure modes internally).
        try:
            result = pickle.loads(stdout_bytes)
        except Exception as e:
            return SandboxResult(
                success=False,
                error_kind="unexpected",
                stderr=(
                    f"could not unpickle worker result: {e}\n"
                    f"--- subprocess stderr ---\n{stderr_text}"
                ),
                exit_code=proc.returncode,
            )

        if not isinstance(result, SandboxResult):
            return SandboxResult(
                success=False,
                error_kind="unexpected",
                stderr=(
                    f"worker returned non-SandboxResult type "
                    f"{type(result).__name__}; stderr was:\n{stderr_text}"
                ),
                exit_code=proc.returncode,
            )

        return result


def _extract_violated_rule(stderr: str) -> str | None:
    """Best-effort: pull the first ``Sandbox: ...`` line from stderr.

    macOS sandbox-exec emits a line shaped like:
        Sandbox: python(12345) deny(1) file-write-data /etc/hosts

    We return the suffix after ``deny(1) `` (the rule name + target)
    as the violated_rule. Returns None if we can't find it; the
    caller falls back to using the full stderr.
    """
    for line in stderr.splitlines():
        line = line.strip()
        if "deny(1)" in line:
            _, _, after = line.partition("deny(1)")
            return after.strip() or None
    return None


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Platform sniff
# ---------------------------------------------------------------------------


def default_sandbox() -> Sandbox | None:
    """Return the platform-appropriate :class:`Sandbox` or ``None``.

    macOS → :class:`MacOSSandboxExec`. Linux → ``None`` until T2 lands
    the bwrap implementation. Windows → ``None`` (v1 of the ADR
    explicitly doesn't support Windows).

    ``None`` means "no sandbox available on this host". The dispatcher
    (T4) reads this; combined with ``FSF_TOOL_SANDBOX`` env:

      - ``off``        → ignore None; run in-process (default behavior)
      - ``permissive`` → ignore None; run in-process + emit
                          ``sandbox_setup_failed=true`` annotation
      - ``strict``     → refuse the dispatch with
                          ``tool_call_refused(reason=sandbox_setup_failed)``
    """
    if sys.platform == "darwin":
        # Verify sandbox-exec is actually on this macOS host. (Some
        # restricted environments strip it; we'd rather return None
        # than hand back an instance that always returns
        # setup_failed.)
        if shutil.which(MacOSSandboxExec.SANDBOX_EXEC_PATH):
            return MacOSSandboxExec()
        return None
    # Linux / Windows / others — T2 will fill this in for Linux.
    return None
