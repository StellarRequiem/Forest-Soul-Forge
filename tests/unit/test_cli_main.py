"""Argparse-dispatch smoke tests for ``fsf`` CLI root.

Coverage was 0 unit tests at Phase A audit (2026-04-30, finding T-6).
The CLI root + subparser registrations are pure plumbing — but a typo
in argparse setup ships silently and only surfaces when someone tries
to use the affected subcommand. These tests pin the dispatch table.

Strategy: drive ``main()`` with no-side-effect inputs (--help/--version
or known invalid argv) and assert exit codes + stderr/stdout shape.
We deliberately do NOT exercise the underlying runners — those are
covered by their own test files (test_skill_install, test_tool_install,
test_skill_forge, test_tool_forge).
"""
from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest

from forest_soul_forge.cli.main import _build_parser, main


# ---------------------------------------------------------------------------
# Parser construction — must not crash, must register all subcommands
# ---------------------------------------------------------------------------
class TestParserConstruction:
    def test_parser_builds_without_error(self):
        parser = _build_parser()
        assert isinstance(parser, argparse.ArgumentParser)
        assert parser.prog == "fsf"

    def test_top_level_subcommands_registered(self):
        """Every top-level subcommand the README documents must be in
        the parser. Catches accidental removal during refactor."""
        parser = _build_parser()
        # Walk the subparser dict — argparse stores it on the
        # _SubParsersAction's choices dict.
        sub_action = next(
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        commands = set(sub_action.choices.keys())
        # ADR-0030 / 0031:
        assert "forge" in commands
        # ADR-0030 T4 / 0031 T2a:
        assert "install" in commands
        # ADR-0034 SW-track ceremony:
        assert "triune" in commands
        # ADR-003X K5 chronicle export:
        assert "chronicle" in commands

    def test_forge_subtree_has_tool_and_skill(self):
        parser = _build_parser()
        # Drill: parser → 'forge' → its own subparsers → {'tool', 'skill'}
        sub_action = next(
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        forge = sub_action.choices["forge"]
        forge_sub_action = next(
            a for a in forge._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        assert {"tool", "skill"}.issubset(set(forge_sub_action.choices.keys()))

    def test_install_subtree_has_tool_and_skill(self):
        parser = _build_parser()
        sub_action = next(
            a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        install = sub_action.choices["install"]
        install_sub_action = next(
            a for a in install._actions
            if isinstance(a, argparse._SubParsersAction)
        )
        assert {"tool", "skill"}.issubset(set(install_sub_action.choices.keys()))


# ---------------------------------------------------------------------------
# Top-level argv handling
# ---------------------------------------------------------------------------
class TestTopLevelDispatch:
    def test_no_subcommand_returns_2_and_prints_help(self, capsys):
        """argparse forces a subcommand (sub.required = True). Without
        one, parse_args raises SystemExit(2)."""
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 2

    def test_version_flag_exits_zero(self, capsys):
        """--version prints + SystemExit(0)."""
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr()
        assert "fsf" in out.out

    def test_unknown_subcommand_returns_2(self, capsys):
        """argparse raises SystemExit(2) on unrecognized subcommand."""
        with pytest.raises(SystemExit) as exc:
            main(["definitely_not_a_command"])
        assert exc.value.code == 2

    def test_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr()
        # Top-level usage banner must mention every documented subcommand.
        for cmd in ("forge", "install", "triune", "chronicle"):
            assert cmd in out.out


# ---------------------------------------------------------------------------
# Subcommand --help paths — exercise each subparser without running it
# ---------------------------------------------------------------------------
class TestSubcommandHelp:
    """Each subcommand's --help must SystemExit(0) with usage text. If
    a subparser registration breaks, this is where it surfaces — long
    before an operator hits the runtime path."""

    def test_forge_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["forge", "--help"])
        assert exc.value.code == 0

    def test_forge_tool_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["forge", "tool", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        # Spot-check a flag the README and runbook reference:
        assert "--dry-run" in out

    def test_forge_skill_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["forge", "skill", "--help"])
        assert exc.value.code == 0

    def test_install_tool_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["install", "tool", "--help"])
        assert exc.value.code == 0

    def test_install_skill_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["install", "skill", "--help"])
        assert exc.value.code == 0

    def test_triune_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["triune", "--help"])
        assert exc.value.code == 0

    def test_chronicle_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["chronicle", "--help"])
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# Runner-dispatch contract — main routes to the right runner
# ---------------------------------------------------------------------------
class TestRunnerDispatch:
    """When parse_args succeeds and a runner is bound to args._run,
    main() invokes it. We mock each runner to verify the wiring is
    correct without actually executing it."""

    def test_forge_tool_runner_invoked(self):
        with mock.patch(
            "forest_soul_forge.cli.forge_tool.run", return_value=0,
        ) as runner:
            rc = main(["forge", "tool", "do something"])
        assert rc == 0
        runner.assert_called_once()

    def test_forge_skill_runner_invoked(self):
        with mock.patch(
            "forest_soul_forge.cli.forge_skill.run", return_value=0,
        ) as runner:
            rc = main(["forge", "skill", "morning sweep"])
        assert rc == 0
        runner.assert_called_once()

    def test_install_tool_runner_invoked(self):
        with mock.patch(
            "forest_soul_forge.cli.install.run_tool", return_value=0,
        ) as runner:
            rc = main(["install", "tool", "/some/path"])
        assert rc == 0
        runner.assert_called_once()

    def test_install_skill_runner_invoked(self):
        with mock.patch(
            "forest_soul_forge.cli.install.run_skill", return_value=0,
        ) as runner:
            rc = main(["install", "skill", "/some/manifest.yaml"])
        assert rc == 0
        runner.assert_called_once()

    def test_runner_nonzero_propagates(self):
        """When a runner returns nonzero, main returns the same code
        (so shell scripts can react to failures)."""
        with mock.patch(
            "forest_soul_forge.cli.forge_tool.run", return_value=42,
        ):
            rc = main(["forge", "tool", "x"])
        assert rc == 42

    def test_runner_returning_none_treated_as_zero(self):
        """``int(None or 0) == 0`` — a runner that forgets to return
        anything is treated as success rather than crashing."""
        with mock.patch(
            "forest_soul_forge.cli.forge_tool.run", return_value=None,
        ):
            rc = main(["forge", "tool", "x"])
        assert rc == 0

    def test_keyboard_interrupt_returns_130(self):
        """Ctrl-C during a runner returns 130 (POSIX SIGINT exit code)."""
        with mock.patch(
            "forest_soul_forge.cli.forge_tool.run",
            side_effect=KeyboardInterrupt(),
        ):
            rc = main(["forge", "tool", "x"])
        assert rc == 130
