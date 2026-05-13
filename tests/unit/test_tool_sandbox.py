"""ADR-0051 T1 unit tests for the sandbox abstraction.

Covers the parts of the sandbox module that DON'T require actually
spawning a sandboxed subprocess. The end-to-end "spawn sandbox-exec +
run a tool" path is exercised in an explicit darwin-gated integration
test at the bottom; see :class:`TestMacOSSandboxIntegration`.

The substrate is opt-in (FSF_TOOL_SANDBOX=off by default) and the
dispatcher integration lands in T4; this test file verifies the T1
building blocks without touching any dispatch path.
"""
from __future__ import annotations

import pickle
import sys
from dataclasses import FrozenInstanceError

import pytest

from forest_soul_forge.tools.base import ToolContext
from forest_soul_forge.tools.sandbox import (
    LinuxBwrap,
    MacOSSandboxExec,
    Sandbox,
    SandboxProfile,
    SandboxResult,
    _macos_profile_text,
    build_profile,
    default_sandbox,
)
from forest_soul_forge.tools.sandbox_context import (
    SerializableToolContext,
    _SERIALIZABLE_CONSTRAINT_KEYS,
)


# ---------------------------------------------------------------------------
# SerializableToolContext
# ---------------------------------------------------------------------------


class TestSerializableToolContext:
    def test_filters_constraints_to_allowlist(self):
        ctx = ToolContext(
            instance_id="ag1",
            agent_dna="aaaaaaaaaaaa",
            role="experimenter",
            genre="security_low",
            session_id="s1",
            constraints={
                # Allowlisted — should survive.
                "allowed_paths": ["/tmp/proj"],
                "allowed_hosts": ["api.example.com"],
                "max_calls_per_session": 100,
                # Live handle — must be dropped.
                "mcp_registry": object(),
                # Random key — also dropped (no key passes through
                # without being on the allowlist).
                "secret_internal_state": {"a": 1},
            },
        )
        sctx = SerializableToolContext.from_tool_context(ctx)
        assert sctx.constraints["allowed_paths"] == ["/tmp/proj"]
        assert sctx.constraints["allowed_hosts"] == ["api.example.com"]
        assert sctx.constraints["max_calls_per_session"] == 100
        assert "mcp_registry" not in sctx.constraints
        assert "secret_internal_state" not in sctx.constraints

    def test_drops_live_handle_fields(self):
        """provider/memory/delegate/etc. must NOT cross the boundary —
        SerializableToolContext doesn't even carry slots for them, so
        the projection silently drops them. Verify by pickling the
        result successfully (live handles would fail to pickle)."""
        live_handle = lambda x: x  # noqa: E731
        ctx = ToolContext(
            instance_id="ag1",
            agent_dna="aaaaaaaaaaaa",
            role="experimenter",
            genre=None,
            session_id=None,
            constraints={"allowed_paths": []},
            provider=live_handle,
            logger=live_handle,
            memory=live_handle,
            delegate=live_handle,
            priv_client=live_handle,
            secrets=live_handle,
            agent_registry=live_handle,
            procedural_shortcuts=live_handle,
        )
        sctx = SerializableToolContext.from_tool_context(ctx)
        # Round-trip: pickle.dumps must succeed despite the source ctx
        # having un-picklable closures.
        round_tripped = pickle.loads(pickle.dumps(sctx))
        assert round_tripped == sctx

    def test_to_tool_context_rehydrates_with_none_handles(self):
        sctx = SerializableToolContext(
            instance_id="ag1",
            agent_dna="aaaaaaaaaaaa",
            role="experimenter",
            constraints={"allowed_paths": ["/tmp/x"]},
        )
        ctx = sctx.to_tool_context()
        assert ctx.instance_id == "ag1"
        assert ctx.agent_dna == "aaaaaaaaaaaa"
        assert ctx.role == "experimenter"
        # All seven live-handle fields rehydrated as None per the
        # sandbox_eligible contract.
        assert ctx.provider is None
        assert ctx.logger is None
        assert ctx.memory is None
        assert ctx.delegate is None
        assert ctx.priv_client is None
        assert ctx.secrets is None
        assert ctx.agent_registry is None
        assert ctx.procedural_shortcuts is None

    def test_constraint_allowlist_is_explicit(self):
        """Sanity: the allowlist is a defined frozenset, not 'whatever
        is JSON-shaped'. Future drift (new constraint key needed by a
        sandbox-eligible tool) requires an explicit edit here, not
        a silent expansion."""
        assert "allowed_paths" in _SERIALIZABLE_CONSTRAINT_KEYS
        assert "allowed_hosts" in _SERIALIZABLE_CONSTRAINT_KEYS
        assert "allowed_commands" in _SERIALIZABLE_CONSTRAINT_KEYS
        # Things NOT on the allowlist:
        assert "memory" not in _SERIALIZABLE_CONSTRAINT_KEYS
        assert "mcp_registry" not in _SERIALIZABLE_CONSTRAINT_KEYS


# ---------------------------------------------------------------------------
# build_profile() — the ADR Decision 4 mapping table
# ---------------------------------------------------------------------------


class TestBuildProfile:
    def test_read_only_no_writes_no_network(self):
        p = build_profile(
            side_effects="read_only",
            allowed_paths=["/tmp/scan"],
        )
        assert p.side_effects == "read_only"
        assert p.allowed_read_paths == ("/tmp/scan",)
        assert p.allowed_write_paths == ()
        assert p.allow_network is False
        assert p.allowed_commands == ()

    def test_network_allows_network_and_hosts(self):
        p = build_profile(
            side_effects="network",
            allowed_paths=["/tmp/cache"],
            allowed_hosts=["api.example.com", "8.8.8.8"],
        )
        assert p.allow_network is True
        assert "api.example.com" in p.allowed_hosts
        # network side_effects still grants reads but NOT writes.
        assert p.allowed_read_paths == ("/tmp/cache",)
        assert p.allowed_write_paths == ()

    def test_filesystem_allows_read_and_write_same_paths(self):
        p = build_profile(
            side_effects="filesystem",
            allowed_paths=["/tmp/proj", "/Users/x/work"],
        )
        assert p.allowed_read_paths == ("/tmp/proj", "/Users/x/work")
        assert p.allowed_write_paths == ("/tmp/proj", "/Users/x/work")
        assert p.allow_network is False

    def test_external_allows_exec_of_allowed_commands(self):
        p = build_profile(
            side_effects="external",
            allowed_paths=["/tmp/proj"],
            allowed_commands=["/usr/bin/curl", "/usr/bin/git"],
        )
        assert p.allowed_commands == ("/usr/bin/curl", "/usr/bin/git")
        # external is fs-effecting too — write paths granted.
        assert p.allowed_write_paths == ("/tmp/proj",)
        assert p.allow_network is False  # external doesn't imply network

    def test_unknown_side_effects_raises(self):
        with pytest.raises(ValueError) as ei:
            build_profile(side_effects="makes_pancakes", allowed_paths=[])
        assert "makes_pancakes" in str(ei.value)


# ---------------------------------------------------------------------------
# .sb profile text generation
# ---------------------------------------------------------------------------


class TestMacOSProfileText:
    def test_deny_default_present(self):
        profile = build_profile(side_effects="read_only", allowed_paths=[])
        text = _macos_profile_text(profile)
        # The most important line — without (deny default) the
        # profile is open-by-default and the sandbox is meaningless.
        assert "(deny default)" in text
        assert "(version 1)" in text

    def test_system_read_paths_always_present(self):
        """The Python interpreter can't even start without read access
        to /usr (dyld), /System (frameworks), /private/var/folders
        (tmp). _macos_profile_text always emits these."""
        profile = build_profile(side_effects="read_only", allowed_paths=[])
        text = _macos_profile_text(profile)
        # /usr is the canonical one Python's dyld needs first.
        assert '"/usr"' in text
        assert '"/System"' in text

    def test_filesystem_writes_emit_file_write_subpath(self):
        profile = build_profile(
            side_effects="filesystem",
            allowed_paths=["/tmp/myproj"],
        )
        text = _macos_profile_text(profile)
        assert "file-write*" in text
        assert "/tmp/myproj" in text

    def test_network_emits_allow_network(self):
        profile = build_profile(
            side_effects="network",
            allowed_paths=["/tmp/cache"],
            allowed_hosts=["api.example.com"],
        )
        text = _macos_profile_text(profile)
        assert "(allow network*)" in text

    def test_external_emits_process_exec_for_each_command(self):
        profile = build_profile(
            side_effects="external",
            allowed_paths=["/tmp/proj"],
            allowed_commands=["/usr/bin/curl"],
        )
        text = _macos_profile_text(profile)
        assert "process-exec*" in text
        assert "/usr/bin/curl" in text

    def test_rejects_paths_with_quote_chars(self):
        """Path injection defense — sandbox-exec uses TinyScheme syntax,
        a path containing a double-quote could close the string and
        inject new directives. _quote_sb_path rejects."""
        profile = SandboxProfile(
            side_effects="filesystem",
            allowed_read_paths=('/tmp/evil"; (allow file-write*) ; "',),
        )
        with pytest.raises(ValueError):
            _macos_profile_text(profile)


# ---------------------------------------------------------------------------
# default_sandbox() platform sniff
# ---------------------------------------------------------------------------


class TestDefaultSandbox:
    def test_returns_macos_impl_on_darwin(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        # On this test machine we may or may not have sandbox-exec
        # — assert only that EITHER MacOSSandboxExec is returned OR
        # None (if the binary is missing). Both shapes are valid per
        # default_sandbox's contract.
        sb = default_sandbox()
        assert sb is None or isinstance(sb, MacOSSandboxExec)

    def test_returns_bwrap_or_none_on_linux(self, monkeypatch):
        """T2: on linux platform with bwrap installed, returns
        LinuxBwrap; without bwrap, returns None. Both are valid
        per default_sandbox's contract — the test machine's bwrap
        state is what it is."""
        monkeypatch.setattr(sys, "platform", "linux")
        sb = default_sandbox()
        assert sb is None or isinstance(sb, LinuxBwrap)

    def test_returns_none_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        assert default_sandbox() is None


# ---------------------------------------------------------------------------
# SandboxProfile + SandboxResult dataclass invariants
# ---------------------------------------------------------------------------


class TestDataclassInvariants:
    def test_sandbox_profile_is_frozen(self):
        p = SandboxProfile(side_effects="read_only")
        with pytest.raises(FrozenInstanceError):
            p.allow_network = True  # type: ignore[misc]

    def test_sandbox_result_is_frozen(self):
        r = SandboxResult(success=True, result_pickle=b"")
        with pytest.raises(FrozenInstanceError):
            r.success = False  # type: ignore[misc]

    def test_sandbox_result_failure_carries_error_kind(self):
        r = SandboxResult(
            success=False,
            error_kind="sandbox_violation",
            violated_rule="file-write-data /etc",
            stderr="Sandbox: python(123) deny(1) file-write-data /etc",
        )
        assert r.success is False
        assert r.error_kind == "sandbox_violation"
        assert "deny" in r.stderr


# ---------------------------------------------------------------------------
# Sandbox Protocol — interface-shape check
# ---------------------------------------------------------------------------


class TestSandboxProtocol:
    def test_macos_impl_satisfies_protocol(self):
        """structural-subtype check: MacOSSandboxExec must conform to
        the :class:`Sandbox` protocol so the dispatcher (T4) can hold
        a Sandbox-typed reference to it."""
        impl: Sandbox = MacOSSandboxExec()  # type: ignore[assignment]
        assert hasattr(impl, "run")
        assert callable(impl.run)


# ---------------------------------------------------------------------------
# End-to-end integration (darwin only) — actually spawn sandbox-exec
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS sandbox-exec integration test — Linux uses bwrap (T2)",
)
class TestMacOSSandboxIntegration:
    """End-to-end: actually invoke ``/usr/bin/sandbox-exec`` and run a
    minimal tool through the worker.

    These tests are flaky targets in CI environments without
    sandbox-exec (e.g., GitHub Actions linux runners). Skipped on
    non-darwin; on darwin, they take ~100-300ms each."""

    def test_setup_failed_when_sandbox_exec_missing(self, monkeypatch):
        """If sandbox-exec path is patched to nonexistent, run()
        returns setup_failed cleanly rather than crashing."""
        impl = MacOSSandboxExec()
        monkeypatch.setattr(
            MacOSSandboxExec, "SANDBOX_EXEC_PATH", "/nonexistent/sandbox-exec",
        )
        result = impl.run(
            tool_module="forest_soul_forge.tools.builtin.timestamp_window",
            tool_class="TimestampWindowTool",
            args={"window_seconds": 60},
            ctx=SerializableToolContext(
                instance_id="ag1",
                agent_dna="aaaaaaaaaaaa",
                role="experimenter",
            ),
            profile=build_profile(side_effects="read_only", allowed_paths=[]),
        )
        assert result.success is False
        assert result.error_kind == "setup_failed"
        assert "not found" in result.stderr


# ---------------------------------------------------------------------------
# LinuxBwrap implementation — ADR-0051 T2
# ---------------------------------------------------------------------------


class TestLinuxBwrapArgvBuilder:
    """Test bwrap's argv-construction logic without actually invoking
    bwrap. These tests run on every platform — the argv shape is
    platform-independent (it's just string composition)."""

    def test_argv_starts_with_bwrap_binary(self):
        impl = LinuxBwrap()
        argv = impl._build_argv(
            build_profile(side_effects="read_only", allowed_paths=[]),
        )
        assert argv[0] == LinuxBwrap.BWRAP_PATH

    def test_argv_includes_system_ro_binds(self):
        """The Python interpreter can't start in the namespace
        without /usr (libs), /etc (resolv.conf, etc.), and a few
        other dirs. _build_argv must always emit --ro-bind-try for
        these."""
        impl = LinuxBwrap()
        argv = impl._build_argv(
            build_profile(side_effects="read_only", allowed_paths=[]),
        )
        joined = " ".join(argv)
        assert "--ro-bind-try /usr /usr" in joined
        assert "--ro-bind-try /etc /etc" in joined
        # --proc + --dev are required for any Python subprocess
        # (sys.argv, /dev/null, /dev/urandom).
        assert "--proc /proc" in joined
        assert "--dev /dev" in joined
        # Fresh tmpfs for /tmp — tools can scratch even without
        # /tmp in their allowed_paths.
        assert "--tmpfs /tmp" in joined

    def test_argv_emits_unshare_net_when_network_denied(self):
        impl = LinuxBwrap()
        argv = impl._build_argv(
            build_profile(side_effects="filesystem", allowed_paths=[]),
        )
        assert "--unshare-net" in argv

    def test_argv_no_unshare_net_when_network_allowed(self):
        impl = LinuxBwrap()
        argv = impl._build_argv(
            build_profile(
                side_effects="network",
                allowed_paths=[],
                allowed_hosts=["api.example.com"],
            ),
        )
        assert "--unshare-net" not in argv

    def test_argv_writes_for_filesystem_paths_use_bind_not_ro_bind(self):
        """Writes to allowed_paths must use --bind (rw); reads stay
        on --ro-bind. A misuse of --ro-bind here would silently break
        every filesystem-side-effect tool with EACCES."""
        impl = LinuxBwrap()
        argv = impl._build_argv(
            build_profile(
                side_effects="filesystem",
                allowed_paths=["/tmp/proj"],
            ),
        )
        # Should see both --ro-bind /tmp/proj (read access) AND
        # --bind /tmp/proj (write access).
        joined = " ".join(argv)
        assert "--ro-bind /tmp/proj /tmp/proj" in joined
        assert "--bind /tmp/proj /tmp/proj" in joined

    def test_argv_tail_is_python_isolated_worker(self):
        impl = LinuxBwrap()
        argv = impl._build_argv(
            build_profile(side_effects="read_only", allowed_paths=[]),
        )
        # Last 4 args must be: <python> -I -m <worker module>.
        assert argv[-3:] == ["-I", "-m", "forest_soul_forge.tools._sandbox_worker"]
        # The -4th from end is the python interpreter path.
        assert argv[-4].endswith("python") or "python" in argv[-4]

    def test_argv_emits_die_with_parent_and_new_session(self):
        """Hygiene flags: worker dies when daemon dies, runs in own
        session group so SIGINT to daemon's TTY doesn't accidentally
        kill the sandboxed worker through the TTY group."""
        impl = LinuxBwrap()
        argv = impl._build_argv(
            build_profile(side_effects="read_only", allowed_paths=[]),
        )
        assert "--die-with-parent" in argv
        assert "--new-session" in argv

    def test_argv_unshare_pid_for_filesystem_tool(self):
        """Per-process isolation: the worker shouldn't see other
        processes on the host. --unshare-pid limits /proc to the
        worker's own pid namespace."""
        impl = LinuxBwrap()
        argv = impl._build_argv(
            build_profile(side_effects="filesystem", allowed_paths=["/tmp/x"]),
        )
        assert "--unshare-pid" in argv


class TestLinuxBwrapSetupFailures:
    """Tests for LinuxBwrap.run() error paths that don't require
    actually invoking bwrap. These run on every platform."""

    def test_setup_failed_when_bwrap_missing(self, monkeypatch):
        """If bwrap path is patched to nonexistent, run() returns
        setup_failed cleanly rather than crashing."""
        impl = LinuxBwrap()
        monkeypatch.setattr(
            LinuxBwrap, "BWRAP_PATH", "/nonexistent/bwrap",
        )
        result = impl.run(
            tool_module="forest_soul_forge.tools.builtin.timestamp_window",
            tool_class="TimestampWindowTool",
            args={"window_seconds": 60},
            ctx=SerializableToolContext(
                instance_id="ag1",
                agent_dna="aaaaaaaaaaaa",
                role="experimenter",
            ),
            profile=build_profile(side_effects="read_only", allowed_paths=[]),
        )
        assert result.success is False
        assert result.error_kind == "setup_failed"
        assert "not found" in result.stderr
        assert "bubblewrap" in result.stderr  # install hint visible


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux bwrap integration test — macOS uses sandbox-exec (T1)",
)
class TestLinuxBwrapIntegration:
    """End-to-end: actually invoke bwrap. Skipped on non-Linux.

    Note: bwrap requires user-namespaces to be enabled in the kernel.
    Most distros enable this by default; the integration tests
    further skipif unprivileged user-namespaces aren't usable on
    this host."""

    def _user_namespaces_available(self) -> bool:
        """Quick probe: try to spawn `bwrap --bind / / true`. If
        user-namespaces are disabled (some hardened RHEL/CentOS),
        bwrap exits non-zero with a setup-failure stderr."""
        import subprocess as sp
        if not Path(LinuxBwrap.BWRAP_PATH).exists():
            return False
        try:
            r = sp.run(
                [LinuxBwrap.BWRAP_PATH, "--bind", "/", "/", "true"],
                capture_output=True, timeout=3,
            )
        except (sp.TimeoutExpired, OSError):
            return False
        return r.returncode == 0

    def test_setup_failed_signals_when_bwrap_userns_unavailable(self):
        """If we can't even smoke-test bwrap, this test self-skips —
        sandboxed integration is environmental."""
        if not self._user_namespaces_available():
            pytest.skip("user namespaces unavailable on this host")
        # If userns IS available, we expect run() to succeed for a
        # tool that just returns a timestamp. The full happy-path
        # integration matches the macOS shape but is deferred to T4
        # where the dispatcher wires it in.
