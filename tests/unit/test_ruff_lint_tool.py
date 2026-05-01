"""Tests for ruff_lint.v1 (Phase G.1.A first programming primitive).

Coverage:
- TestValidate            — input shape: path, max_findings, timeout_seconds
- TestLocateRuff          — _locate_ruff helper (PATH vs python -m fallback)
- TestNormalizeFinding    — ruff JSON shape -> FSF stable schema mapping
- TestPathAllowlist       — allowed_paths gate; mirrors code_read.v1 semantics
- TestExecute             — happy path, no-issues, syntax errors, missing path,
                            timeout, ruff-not-installed scenarios
- TestRegistration        — tool registers via register_builtins
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.ruff_lint import (
    DEFAULT_MAX_FINDINGS,
    DEFAULT_TIMEOUT_SECONDS,
    RuffLintError,
    RuffLintTool,
    RuffNotInstalledError,
    _is_within_any,
    _locate_ruff,
    _normalize_finding,
    _resolve_allowlist,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    """A temp directory with one Python file containing intentional
    lint issues. Returns (ctx, file_path)."""
    fp = tmp_path / "sample.py"
    fp.write_text(
        # F401: unused imports; E225: spacing
        "import os\nimport sys\nx=1+2\nprint( x )\n",
        encoding="utf-8",
    )
    ctx = ToolContext(
        instance_id="i1", agent_dna="d" * 12,
        role="code_reviewer", genre="guardian",
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
            RuffLintTool().validate({})

    def test_empty_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            RuffLintTool().validate({"path": "  "})

    def test_non_string_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            RuffLintTool().validate({"path": 42})

    def test_invalid_max_findings_rejected(self):
        with pytest.raises(ToolValidationError, match="max_findings"):
            RuffLintTool().validate({"path": "/tmp", "max_findings": 0})
        with pytest.raises(ToolValidationError, match="max_findings"):
            RuffLintTool().validate({"path": "/tmp", "max_findings": 100_001})
        with pytest.raises(ToolValidationError, match="max_findings"):
            RuffLintTool().validate({"path": "/tmp", "max_findings": "many"})

    def test_invalid_timeout_rejected(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            RuffLintTool().validate({"path": "/tmp", "timeout_seconds": 0})
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            RuffLintTool().validate({"path": "/tmp", "timeout_seconds": 999})
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            RuffLintTool().validate({"path": "/tmp", "timeout_seconds": 1.5})

    def test_valid_minimal_args_accepted(self):
        # Just a path; defaults for the rest.
        RuffLintTool().validate({"path": "/tmp/foo"})

    def test_valid_full_args_accepted(self):
        RuffLintTool().validate(
            {"path": "/tmp/foo", "max_findings": 100, "timeout_seconds": 60}
        )


# ===========================================================================
# Helpers
# ===========================================================================
class TestLocateRuff:
    def test_locates_ruff_when_available(self):
        # ruff was installed for this test environment via pip.
        # Either `ruff` is on PATH or `python3 -m ruff` works.
        invocation = _locate_ruff()
        assert invocation is not None
        assert isinstance(invocation, tuple)

    def test_returns_none_when_ruff_unavailable(self):
        # Simulate complete absence of ruff: shutil.which returns None,
        # python3 -m ruff fails. Use mock for both.
        with mock.patch(
            "forest_soul_forge.tools.builtin.ruff_lint.shutil.which",
            return_value=None,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.ruff_lint.subprocess.run",
            side_effect=FileNotFoundError("no python3"),
        ):
            assert _locate_ruff() is None


class TestNormalizeFinding:
    def test_full_finding_shape(self):
        raw = {
            "code": "F401",
            "name": "unused-import",
            "message": "`os` imported but unused",
            "filename": "/tmp/sample.py",
            "location": {"row": 1, "column": 8},
            "fix": {"applicability": "safe"},
        }
        out = _normalize_finding(raw)
        assert out == {
            "filename":  "/tmp/sample.py",
            "line":      1,
            "column":    8,
            "rule_code": "F401",
            "rule_name": "unused-import",
            "message":   "`os` imported but unused",
            "severity":  "violation",
            "fixable":   True,
        }

    def test_finding_without_fix(self):
        out = _normalize_finding(
            {"code": "E501", "message": "line too long",
             "filename": "x.py", "location": {"row": 5, "column": 100}}
        )
        assert out["fixable"] is False
        assert out["rule_code"] == "E501"

    def test_finding_without_name_falls_back_to_code(self):
        out = _normalize_finding(
            {"code": "E501", "message": "...",
             "filename": "x.py", "location": {"row": 1, "column": 1}}
        )
        assert out["rule_name"] == "E501"   # falls back to code

    def test_finding_with_missing_fields_no_crash(self):
        # Pathological input — ruff shouldn't produce this, but defensive.
        out = _normalize_finding({})
        assert out["filename"] == ""
        assert out["line"] == 0
        assert out["column"] == 0
        assert out["rule_code"] == ""


class TestPathAllowlist:
    def test_resolve_skips_empty_strings(self):
        result = _resolve_allowlist(["/tmp", "", None, "  "])
        assert all(isinstance(p, Path) for p in result)
        # Empty string and None get skipped; "/tmp" and "  " (stripped to empty)
        # stay or get filtered. Spec: empty strings (whitespace-only included)
        # are skipped.
        assert len(result) == 1

    def test_is_within_allows_root_match(self, tmp_path):
        result = _is_within_any(tmp_path, (tmp_path,))
        assert result is True

    def test_is_within_allows_descendant(self, tmp_path):
        child = tmp_path / "subdir" / "file.py"
        child.parent.mkdir()
        child.write_text("# x")
        # Resolve to match what the tool does
        assert _is_within_any(child.resolve(), (tmp_path.resolve(),)) is True

    def test_is_within_blocks_outside_path(self, tmp_path):
        # /tmp is not inside tmp_path (or vice versa generally)
        outside = Path("/etc")
        assert _is_within_any(outside, (tmp_path,)) is False


# ===========================================================================
# Execution
# ===========================================================================
class TestExecute:
    def test_lint_file_with_findings(self, env):
        ctx, fp = env
        result = _run(RuffLintTool().execute({"path": str(fp)}, ctx))
        # The sample file has known issues (F401 unused imports + spacing)
        assert result.output["findings_count"] >= 2
        assert result.output["truncated"] is False
        # Exit 1 = findings present (per ruff convention)
        assert result.output["exit_code"] == 1
        # F401 should be among the findings
        rule_codes = {f["rule_code"] for f in result.output["findings"]}
        assert "F401" in rule_codes

    def test_lint_clean_file_returns_no_findings(self, env, tmp_path):
        ctx, _ = env
        clean = tmp_path / "clean.py"
        clean.write_text("'''module docstring.'''\n", encoding="utf-8")
        result = _run(RuffLintTool().execute({"path": str(clean)}, ctx))
        assert result.output["findings_count"] == 0
        assert result.output["exit_code"] == 0

    def test_lint_directory(self, env, tmp_path):
        ctx, _ = env
        # Directory mode — ruff lints all .py inside
        result = _run(RuffLintTool().execute({"path": str(tmp_path)}, ctx))
        # We have one file with intentional issues; expect ≥1 finding
        assert result.output["findings_count"] >= 1

    def test_max_findings_truncates(self, env):
        ctx, fp = env
        # Force truncation by capping at 1; sample has multiple findings
        result = _run(RuffLintTool().execute(
            {"path": str(fp), "max_findings": 1}, ctx,
        ))
        assert result.output["findings_count"] == 1
        assert result.output["truncated"] is True
        # Metadata records the actual count for forensic value
        assert result.metadata["actual_count"] >= 2
        assert result.metadata["max_findings"] == 1

    def test_missing_allowed_paths_refuses(self, tmp_path):
        fp = tmp_path / "x.py"
        fp.write_text("import os\n")
        ctx = ToolContext(
            instance_id="i1", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={},   # no allowed_paths
        )
        with pytest.raises(RuffLintError, match="allowed_paths"):
            _run(RuffLintTool().execute({"path": str(fp)}, ctx))

    def test_path_outside_allowed_refuses(self, tmp_path):
        # Build a target outside the allowed dir
        outside = tmp_path / "inside" / "x.py"
        outside.parent.mkdir()
        outside.write_text("import os\n")
        # Allow a DIFFERENT directory than where the file is
        other_dir = tmp_path / "elsewhere"
        other_dir.mkdir()
        ctx = ToolContext(
            instance_id="i1", agent_dna="d" * 12,
            role="r", genre="g", session_id="s",
            constraints={"allowed_paths": [str(other_dir)]},
        )
        with pytest.raises(RuffLintError, match="outside the agent's allowed_paths"):
            _run(RuffLintTool().execute({"path": str(outside)}, ctx))

    def test_nonexistent_path_refuses(self, env):
        ctx, _ = env
        with pytest.raises(RuffLintError, match="does not exist"):
            _run(RuffLintTool().execute(
                {"path": "/tmp/definitely_not_a_real_path_zzz.py"}, ctx,
            ))

    def test_ruff_not_installed_refuses_cleanly(self, env):
        ctx, fp = env
        # Mock _locate_ruff to return None (simulating unavailable ruff)
        with mock.patch(
            "forest_soul_forge.tools.builtin.ruff_lint._locate_ruff",
            return_value=None,
        ):
            with pytest.raises(RuffNotInstalledError, match="not installed"):
                _run(RuffLintTool().execute({"path": str(fp)}, ctx))

    def test_ruff_subprocess_timeout_refuses(self, env):
        ctx, fp = env
        # Mock _locate_ruff to return a known invocation (so we don't fall
        # through to "ruff not installed"), then mock subprocess.run to
        # raise TimeoutExpired on the actual lint call.
        with mock.patch(
            "forest_soul_forge.tools.builtin.ruff_lint._locate_ruff",
            return_value=("ruff",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.ruff_lint.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ruff", timeout=30),
        ):
            with pytest.raises(RuffLintError, match="timed out"):
                _run(RuffLintTool().execute(
                    {"path": str(fp), "timeout_seconds": 30}, ctx,
                ))

    def test_ruff_hard_error_refuses(self, env):
        ctx, fp = env
        # Mock _locate_ruff so we don't depend on subprocess.run for it,
        # then mock subprocess.run to return exit code 2 (ruff hard error).
        with mock.patch(
            "forest_soul_forge.tools.builtin.ruff_lint._locate_ruff",
            return_value=("ruff",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.ruff_lint.subprocess.run",
            return_value=mock.MagicMock(
                returncode=2,
                stdout="",
                stderr="something broke",
            ),
        ):
            with pytest.raises(RuffLintError, match="exited with code 2"):
                _run(RuffLintTool().execute({"path": str(fp)}, ctx))

    def test_metadata_records_invocation(self, env):
        ctx, fp = env
        result = _run(RuffLintTool().execute({"path": str(fp)}, ctx))
        # Forensic value: operator can see exactly which ruff invocation ran
        assert "ruff_invocation" in result.metadata
        assert isinstance(result.metadata["ruff_invocation"], list)
        # First arg is either "ruff" (PATH) or "python3" (module fallback)
        assert result.metadata["ruff_invocation"][0] in ("ruff", "python3")

    def test_findings_have_complete_shape(self, env):
        ctx, fp = env
        result = _run(RuffLintTool().execute({"path": str(fp)}, ctx))
        for f in result.output["findings"]:
            for required in (
                "filename", "line", "column", "rule_code",
                "rule_name", "message", "severity", "fixable",
            ):
                assert required in f, f"missing {required} in {f}"
            assert isinstance(f["line"], int)
            assert isinstance(f["column"], int)
            assert isinstance(f["fixable"], bool)


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_ruff_lint_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("ruff_lint", "1")

    def test_ruff_lint_in_catalog(self):
        from pathlib import Path
        from forest_soul_forge.core.tool_catalog import load_catalog
        cat = load_catalog(
            Path(__file__).parent.parent.parent / "config" / "tool_catalog.yaml"
        )
        assert "ruff_lint.v1" in cat.tools
        td = cat.tools["ruff_lint.v1"]
        assert td.side_effects == "read_only"
