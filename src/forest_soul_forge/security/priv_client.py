"""Daemon-side wrapper around ``/usr/local/sbin/fsf-priv``.

ADR-0033 A6. Each privileged operation is a method on
:class:`PrivClient`; the method shells out via ``sudo`` (NOPASSWD
configured in ``/etc/sudoers.d/fsf``), captures stdout/stderr +
exit code, and surfaces the result as a :class:`PrivResult`.

Defense-in-depth shape:

* The helper script is the **first** line of defense — it has its
  own allowlist on every operation. This client is the **second**
  line: input validation here means the helper's allowlist never
  has to deal with malformed input under normal operation.

* The client refuses to construct invocations that would obviously
  fail the helper's allowlist (anchor character set, rule length,
  PID range). Refusals raise :class:`PrivClientError` BEFORE the
  shell-out so the audit chain doesn't get a refused-helper-call
  it could have prevented.

* The client never passes ``shell=True`` to subprocess — every arg
  is a separate list element, so shell metacharacters in operator-
  supplied input can't escape the argv boundary.

* Helper-not-installed (the operator hasn't run the install
  runbook yet) is signalled distinctly via :class:`HelperMissing`
  so the daemon's lifespan can degrade gracefully rather than 500
  on every isolate_process.v1 dispatch.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


# Defaults match what the runbook installs. Operators can override
# via env or constructor — useful for tests with a mock helper.
_DEFAULT_HELPER = "/usr/local/sbin/fsf-priv"
_DEFAULT_SUDO = "/usr/bin/sudo"

# Mirror the helper's allowlists so we refuse early. Keeping these
# in sync with the helper is the operator's responsibility — the
# install runbook calls this out.
_ALLOWED_ANCHOR_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./"
)
_MAX_ANCHOR_LEN = 64
_MAX_RULE_LEN = 256


class PrivClientError(Exception):
    """Raised when the client can't (or won't) issue an op. Distinct
    from helper-side refusals which surface as ``PrivResult(ok=False,
    exit_code=2, stderr=...)`` — those are the helper saying no, this
    is the client saying no before even shelling out."""


class HelperMissing(PrivClientError):
    """Raised when the configured helper path doesn't exist or isn't
    executable. The daemon's lifespan should catch this and degrade
    privileged tools to "advisor only" rather than failing the
    whole tool dispatcher."""


@dataclass(frozen=True)
class PrivResult:
    """Outcome of a single privileged op call.

    ``ok`` is True iff the helper exited 0. ``exit_code`` is the
    helper's exit code (2 = helper-side refusal, 0 = success, other
    = helper-internal error). ``stdout`` and ``stderr`` are captured
    as strings; tools that wrap PrivClient (isolate_process.v1 etc.)
    surface them in their ToolResult.metadata so the operator can
    see the helper's diagnostics in the approval queue UI.
    """

    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    op: str
    args: tuple[str, ...]


@dataclass
class PrivClient:
    """Shell wrapper around the helper. Constructed once at lifespan;
    held on app.state. Every privileged tool reaches for the same
    instance so the helper-not-installed check and timeout behavior
    live in one place."""

    helper_path: str = _DEFAULT_HELPER
    sudo_path: str = _DEFAULT_SUDO
    timeout_seconds: float = 15.0

    def assert_available(self) -> None:
        """Raise :class:`HelperMissing` if the helper isn't installed.
        Daemon lifespan calls this on startup so the
        privileged-tool-degraded state is visible in /healthz."""
        if not Path(self.helper_path).exists():
            raise HelperMissing(
                f"helper not found at {self.helper_path}. Run the "
                "install runbook (docs/runbooks/sudo-helper-install.md) "
                "to set up the privileged-ops helper."
            )
        if not Path(self.sudo_path).exists():
            raise HelperMissing(f"sudo not found at {self.sudo_path}")

    def _run(self, op: str, *op_args: str) -> PrivResult:
        """Internal: shell out via sudo + helper. Catches subprocess
        exceptions and turns them into PrivResult so callers always
        get a structured outcome."""
        if not Path(self.helper_path).exists():
            raise HelperMissing(self.helper_path)
        cmd = [self.sudo_path, "-n", self.helper_path, op, *op_args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return PrivResult(
                ok=False, exit_code=124,  # 124 = `timeout` convention
                stdout="", stderr=f"helper timed out after {self.timeout_seconds}s",
                op=op, args=tuple(op_args),
            )
        return PrivResult(
            ok=(proc.returncode == 0),
            exit_code=proc.returncode,
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
            op=op,
            args=tuple(op_args),
        )

    # -------- Operations -----------------------------------------------------

    def kill_pid(self, pid: int) -> PrivResult:
        """SIGTERM then SIGKILL a process. Used by isolate_process.v1.

        Refuses (raises PrivClientError) for PID ≤ 1 (kernel/init)
        or for non-int input. The helper has the same check; we
        refuse early so an obvious bug in the calling tool doesn't
        leave a refused-helper line in the audit log.
        """
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            raise PrivClientError(
                f"PID must be a positive integer > 1; got {pid!r}"
            )
        return self._run("kill-pid", str(pid))

    def pf_add(self, anchor: str, rule: str) -> PrivResult:
        """Apply one rule to a pf anchor. Used by dynamic_policy.v1."""
        self._validate_anchor(anchor)
        self._validate_rule(rule)
        return self._run("pf-add", anchor, rule)

    def pf_drop(self, anchor: str) -> PrivResult:
        """Flush a pf anchor. Used by dynamic_policy.v1 to revoke."""
        self._validate_anchor(anchor)
        return self._run("pf-drop", anchor)

    def read_protected(self, path: str) -> PrivResult:
        """Hash + size of a SIP-protected file. Used by tamper_detect.v1
        when SIP-protected reads are enabled. The helper's stdout is
        ``sha256:HEX SIZE PATH`` on success; callers parse with
        :meth:`parse_read_protected_output`."""
        if not isinstance(path, str) or not path.startswith("/"):
            raise PrivClientError(
                f"path must be an absolute string; got {path!r}"
            )
        return self._run("read-protected", path)

    @staticmethod
    def parse_read_protected_output(stdout: str) -> tuple[str, int, str]:
        """Helper for read_protected. Returns (digest, size, path).
        Raises PrivClientError on malformed output (which would
        indicate a helper bug)."""
        line = stdout.strip()
        parts = line.split(" ", 2)
        if len(parts) != 3:
            raise PrivClientError(f"helper output malformed: {line!r}")
        digest, size_str, path = parts
        if not digest.startswith("sha256:"):
            raise PrivClientError(f"digest format unexpected: {digest!r}")
        try:
            size = int(size_str)
        except ValueError:
            raise PrivClientError(f"size not an integer: {size_str!r}")
        return digest, size, path

    # -------- Validation helpers --------------------------------------------

    def _validate_anchor(self, anchor: str) -> None:
        if not isinstance(anchor, str) or not anchor:
            raise PrivClientError("anchor must be a non-empty string")
        if len(anchor) > _MAX_ANCHOR_LEN:
            raise PrivClientError(
                f"anchor must be ≤ {_MAX_ANCHOR_LEN} chars; got {len(anchor)}"
            )
        if not all(c in _ALLOWED_ANCHOR_CHARS for c in anchor):
            raise PrivClientError(
                "anchor contains characters outside [A-Za-z0-9-_./]"
            )

    def _validate_rule(self, rule: str) -> None:
        if not isinstance(rule, str) or not rule:
            raise PrivClientError("rule must be a non-empty string")
        if len(rule) > _MAX_RULE_LEN:
            raise PrivClientError(
                f"rule must be ≤ {_MAX_RULE_LEN} chars; got {len(rule)}"
            )
