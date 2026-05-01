"""Tests for pytest_run.v1 (Phase G.1.A second programming primitive).

Coverage:
- TestValidate         — input shape: path, selectors, timeout,
                          max_failures, max_lines
- TestLocatePytest     — _locate_pytest helper
- TestParseOutput      — _parse_pytest_output mapping pytest's
                          terminal output to FSF schema
- TestPathAllowlist    — allowed_paths gate; mirrors code_read.v1 / ruff_lint.v1
- TestExecute          — happy path, all-pass, mixed, no-tests, syntax
                          error, missing path, timeout, pytest-not-installed,
                          allowed_paths gate, hard error
- TestRegistration     — tool registers via register_builtins
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.pytest_run import (
    DEFAULT_MAX_FAILURES_REPORTED,
    DEFAULT_TIMEOUT_SECONDS,
    PytestNotInstalledError,
    PytestRunError,
    PytestRunTool,
    _is_within_any,
    _locate_pytest,
    _parse_pytest_output,
    _resolve_allowlist,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    """Tmp dir with a test file containing 1 pass + 1 fail + 1 skip."""
    fp = tmp_path / "test_sample.py"
    fp.write_text(
        "def test_passes():\n"
        "    assert 1 + 1 == 2\n"
        "\n"
        "def test_fails():\n"
        "    assert 1 + 1 == 3\n"
        "\n"
        "def test_skipped():\n"
        "    import pytest\n"
        "    pytest.skip('demo')\n",
        encoding="utf-8",
    )
    ctx = ToolContext(
        instance_id="i1", agent_dna="d" * 12,
        role="software_engineer", genre="actuator",
        session_id="s1",
        constraints={"allowed_paths": [str(tmp_path)]},
    )
    return ctx, fp


# ===========================================================================
# Validation
# ===========================================================================
class TestValidate:
    def test_missing_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            PytestRunTool().validate({})

    def test_empty_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            PytestRunTool().validate({"path": ""})

    def test_non_string_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            PytestRunTool().validate({"path": 42})

    def test_selectors_must_be_list_of_strings(self):
        with pytest.raises(ToolValidationError, match="selectors"):
            PytestRunTool().validate({"path": "/tmp", "selectors": "test"})
        with pytest.raises(ToolValidationError, match="selectors"):
            PytestRunTool().validate({"path": "/tmp", "selectors": [1, 2]})

    def test_invalid_timeout_rejected(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            PytestRunTool().validate({"path": "/tmp", "timeout_seconds": 0})
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            PytestRunTool().validate({"path": "/tmp", "timeout_seconds": 9999})

    def test_invalid_max_failures_rejected(self):
        with pytest.raises(ToolValidationError, match="max_failures_reported"):
            PytestRunTool().validate(
                {"path": "/tmp", "max_failures_reported": -1}
            )
        with pytest.raises(ToolValidationError, match="max_failures_reported"):
            PytestRunTool().validate(
                {"path": "/tmp", "max_failures_reported": 1001}
            )

    def test_invalid_max_lines_rejected(self):
        with pytest.raises(ToolValidationError, match="max_failure_lines"):
            PytestRunTool().validate({"path": "/tmp", "max_failure_lines": 0})
        with pytest.raises(ToolValidationError, match="max_failure_lines"):
            PytestRunTool().validate({"path": "/tmp", "max_failure_lines": 501})

    def test_valid_minimal_args_accepted(self):
        PytestRunTool().validate({"path": "/tmp/foo"})

    def test_valid_full_args_accepted(self):
        PytestRunTool().validate({
            "path": "/tmp/foo",
            "selectors": ["-k", "test_x"],
            "timeout_seconds": 600,
            "max_failures_reported": 100,
            "max_failure_lines": 20,
        })


# ===========================================================================
# Helpers
# ===========================================================================
class TestLocatePytest:
    def test_locates_pytest_when_available(self):
        invocation = _locate_pytest()
        assert invocation is not None
        assert isinstance(invocation, tuple)
        # Either python3 -m pytest or pytest
        assert invocation[0] in ("python3", "pytest")

    def test_returns_none_when_unavailable(self):
        # Simulate complete absence: subprocess.run fails for python3 -m
        # AND shutil.which returns None for pytest.
        with mock.patch(
            "forest_soul_forge.tools.builtin.pytest_run.subprocess.run",
            side_effect=FileNotFoundError("no python3"),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.pytest_run.shutil.which",
            return_value=None,
        ):
            assert _locate_pytest() is None


class TestParseOutput:
    def test_all_pass_summary(self):
        stdout = "..\n=========== 2 passed in 0.10s ===========\n"
        parsed = _parse_pytest_output(stdout, "", max_failures=50, max_lines=50)
        assert parsed["passed"] == 2
        assert parsed["failed"] == 0
        assert parsed["skipped"] == 0
        assert parsed["duration_s"] == 0.10

    def test_mixed_summary(self):
        stdout = (
            ".F.s\n"
            "FAILED tests/unit/test_x.py::test_foo - assert 1 == 2\n"
            "=========== 1 failed, 1 passed, 1 skipped in 0.50s ===========\n"
        )
        parsed = _parse_pytest_output(
            stdout, "", max_failures=50, max_lines=50
        )
        assert parsed["failed"] == 1
        assert parsed["passed"] == 1
        assert parsed["skipped"] == 1
        assert len(parsed["failures"]) == 1
        assert parsed["failures"][0]["test_id"] == "tests/unit/test_x.py::test_foo"
        assert "assert 1 == 2" in parsed["failures"][0]["traceback"][0]

    def test_no_tests_collected(self):
        stdout = "=========== no tests ran in 0.01s ===========\n"
        parsed = _parse_pytest_output(stdout, "", max_failures=50, max_lines=50)
        assert parsed["passed"] == 0
        assert parsed["failed"] == 0
        # Don't assert on the summary_line content match — pytest's
        # exact wording varies; we just verify the parser didn't crash.

    def test_collection_error_summary(self):
        stdout = "=========== 1 error in 0.10s ===========\n"
        parsed = _parse_pytest_output(stdout, "", max_failures=50, max_lines=50)
        assert parsed["errors"] == 1

    def test_max_failures_caps_list(self):
        stdout = (
            "FFFF\n"
            "FAILED test_a.py::a - x\n"
            "FAILED test_a.py::b - x\n"
            "FAILED test_a.py::c - x\n"
            "FAILED test_a.py::d - x\n"
            "=========== 4 failed in 0.10s ===========\n"
        )
        parsed = _parse_pytest_output(
            stdout, "", max_failures=2, max_lines=50
        )
        assert parsed["failed"] == 4   # the count is unaffected
        assert len(parsed["failures"]) == 2   # the list is capped
        assert parsed["failures_truncated"] is True

    def test_empty_stdout_doesnt_crash(self):
        parsed = _parse_pytest_output("", "", max_failures=50, max_lines=50)
        assert parsed["passed"] == 0
        assert parsed["failed"] == 0
        assert parsed["summary_line"] == ""


class TestPathAllowlist:
    def test_resolve_skips_empty(self):
        result = _resolve_allowlist(["/tmp", "", None])
        assert len(result) == 1

    def test_is_within_root(self, tmp_path):
        assert _is_within_any(tmp_path, (tmp_path,)) is True

    def test_is_within_descendant(self, tmp_path):
        sub = tmp_path / "x" / "y"
        sub.parent.mkdir()
        sub.write_text("x")
        assert _is_within_any(sub.resolve(), (tmp_path.resolve(),)) is True

    def test_is_within_blocks_outside(self, tmp_path):
        assert _is_within_any(Path("/etc"), (tmp_path,)) is False


# ===========================================================================
# Execution
# ===========================================================================
class TestExecute:
    def test_run_with_mixed_results(self, env):
        ctx, fp = env
        result = _run(PytestRunTool().execute({"path": str(fp)}, ctx))
        o = result.output
        # 1 pass + 1 fail + 1 skip
        assert o["passed"] == 1
        assert o["failed"] == 1
        assert o["skipped"] == 1
        # Pytest exits 1 when any test fails
        assert o["exit_code"] == 1
        # Failure surfaces in the failures list
        failure_ids = [f["test_id"] for f in o["failures"]]
        assert any("test_fails" in fid for fid in failure_ids)

    def test_run_all_pass(self, tmp_path):
        clean = tmp_path / "test_clean.py"
        clean.write_text(
            "def test_one():\n    assert True\n"
            "def test_two():\n    assert 2 == 2\n",
            encoding="utf-8",
        )
        ctx = ToolContext(
            instance_id="i1", agent_dna="d" * 12,
            role="r", genre="actuator", session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        result = _run(PytestRunTool().execute({"path": str(clean)}, ctx))
        assert result.output["passed"] == 2
        assert result.output["failed"] == 0
        assert result.output["exit_code"] == 0

    def test_no_tests_collected(self, tmp_path):
        # Empty test file (no test functions)
        empty = tmp_path / "test_empty.py"
        empty.write_text("# nothing here\n", encoding="utf-8")
        ctx = ToolContext(
            instance_id="i1", agent_dna="d" * 12,
            role="r", genre="actuator", session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        result = _run(PytestRunTool().execute({"path": str(empty)}, ctx))
        # Pytest exits 5 when no tests collected; we treat that as
        # "ran cleanly" (no error raised).
        assert result.output["exit_code"] == 5
        assert result.output["passed"] == 0
        assert result.output["failed"] == 0

    def test_missing_allowed_paths_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i1", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={},
        )
        with pytest.raises(PytestRunError, match="allowed_paths"):
            _run(PytestRunTool().execute({"path": str(tmp_path)}, ctx))

    def test_outside_allowed_refuses(self, tmp_path):
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "test_x.py").write_text("def test_x(): pass")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        ctx = ToolContext(
            instance_id="i1", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={"allowed_paths": [str(allowed)]},
        )
        with pytest.raises(PytestRunError, match="outside the agent's allowed_paths"):
            _run(PytestRunTool().execute(
                {"path": str(outside_dir / "test_x.py")}, ctx,
            ))

    def test_nonexistent_path_refuses(self, env):
        ctx, _ = env
        with pytest.raises(PytestRunError, match="does not exist"):
            _run(PytestRunTool().execute(
                {"path": "/tmp/no_such_path_zzz.py"}, ctx,
            ))

    def test_timeout_refuses(self, env):
        ctx, fp = env
        with mock.patch(
            "forest_soul_forge.tools.builtin.pytest_run._locate_pytest",
            return_value=("python3", "-m", "pytest"),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.pytest_run.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=300),
        ):
            with pytest.raises(PytestRunError, match="timed out"):
                _run(PytestRunTool().execute(
                    {"path": str(fp), "timeout_seconds": 300}, ctx,
                ))

    def test_pytest_not_installed_refuses_cleanly(self, env):
        ctx, fp = env
        with mock.patch(
            "forest_soul_forge.tools.builtin.pytest_run._locate_pytest",
            return_value=None,
        ):
            with pytest.raises(PytestNotInstalledError, match="not installed"):
                _run(PytestRunTool().execute({"path": str(fp)}, ctx))

    def test_pytest_internal_error_refuses(self, env):
        ctx, fp = env
        with mock.patch(
            "forest_soul_forge.tools.builtin.pytest_run._locate_pytest",
            return_value=("pytest",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.pytest_run.subprocess.run",
            return_value=mock.MagicMock(
                returncode=3,    # pytest internal error
                stdout="",
                stderr="internal pytest crash",
            ),
        ):
            with pytest.raises(PytestRunError, match="exited with code 3"):
                _run(PytestRunTool().execute({"path": str(fp)}, ctx))

    def test_metadata_records_invocation(self, env):
        ctx, fp = env
        result = _run(PytestRunTool().execute({"path": str(fp)}, ctx))
        assert "pytest_invocation" in result.metadata
        assert isinstance(result.metadata["pytest_invocation"], list)


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_pytest_run_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("pytest_run", "1")

    def test_pytest_run_in_catalog(self):
        from pathlib import Path
        from forest_soul_forge.core.tool_catalog import load_catalog
        cat = load_catalog(
            Path(__file__).parent.parent.parent / "config" / "tool_catalog.yaml"
        )
        assert "pytest_run.v1" in cat.tools
        td = cat.tools["pytest_run.v1"]
        assert td.side_effects == "filesystem"

    def test_pytest_run_initiative_l4(self):
        from forest_soul_forge.tools.builtin.pytest_run import PytestRunTool
        assert PytestRunTool.required_initiative_level == "L4"
