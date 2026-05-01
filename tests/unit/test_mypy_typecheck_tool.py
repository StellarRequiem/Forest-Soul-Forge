"""Tests for mypy_typecheck.v1 (Phase G.1.A sixth programming primitive)."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.mypy_typecheck import (
    DEFAULT_MAX_FINDINGS,
    DEFAULT_TIMEOUT_SECONDS,
    MYPY_MAX_FINDINGS_HARD_CAP,
    MypyNotInstalledError,
    MypyTypecheckError,
    MypyTypecheckTool,
    _is_within_any,
    _locate_mypy,
    _parse_mypy_output,
    _resolve_allowlist,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    """A temp dir with one Python file containing a deliberate type error."""
    fp = tmp_path / "sample.py"
    fp.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "result: str = add(1, 2)\n",
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
            MypyTypecheckTool().validate({})

    def test_empty_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            MypyTypecheckTool().validate({"path": "  "})

    def test_invalid_max_findings_rejected(self):
        with pytest.raises(ToolValidationError, match="max_findings"):
            MypyTypecheckTool().validate({"path": "/tmp", "max_findings": 0})
        with pytest.raises(ToolValidationError, match="max_findings"):
            MypyTypecheckTool().validate({
                "path": "/tmp",
                "max_findings": MYPY_MAX_FINDINGS_HARD_CAP + 1,
            })

    def test_invalid_timeout_rejected(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            MypyTypecheckTool().validate({"path": "/tmp", "timeout_seconds": 0})
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            MypyTypecheckTool().validate({"path": "/tmp", "timeout_seconds": 301})

    def test_invalid_config_file_rejected(self):
        with pytest.raises(ToolValidationError, match="config_file"):
            MypyTypecheckTool().validate({"path": "/tmp", "config_file": ""})
        with pytest.raises(ToolValidationError, match="config_file"):
            MypyTypecheckTool().validate({"path": "/tmp", "config_file": 42})

    def test_invalid_strict_rejected(self):
        with pytest.raises(ToolValidationError, match="strict"):
            MypyTypecheckTool().validate({"path": "/tmp", "strict": "yes"})

    def test_valid_minimal(self):
        MypyTypecheckTool().validate({"path": "/tmp"})

    def test_valid_full(self):
        MypyTypecheckTool().validate({
            "path": "/tmp/foo",
            "config_file": "/tmp/mypy.ini",
            "strict": True,
            "max_findings": 100,
            "timeout_seconds": 120,
        })


# ===========================================================================
# _locate_mypy
# ===========================================================================
class TestLocateMypy:
    def test_returns_python_module_when_runnable(self):
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="mypy 1.x", stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            return_value=ok,
        ):
            assert _locate_mypy() == ("python3", "-m", "mypy")

    def test_falls_back_to_path(self):
        bad = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            return_value=bad,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.shutil.which",
            return_value="/usr/local/bin/mypy",
        ):
            assert _locate_mypy() == ("mypy",)

    def test_returns_none_when_neither_works(self):
        bad = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            return_value=bad,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.shutil.which",
            return_value=None,
        ):
            assert _locate_mypy() is None


# ===========================================================================
# _parse_mypy_output
# ===========================================================================
class TestParseMypyOutput:
    def test_error_with_column_and_code(self):
        text = (
            "src/foo.py:10:5: error: Argument 1 to \"f\" has incompatible type \"str\"; expected \"int\"  [arg-type]\n"
        )
        out = _parse_mypy_output(text)
        assert len(out) == 1
        f = out[0]
        assert f["filename"] == "src/foo.py"
        assert f["line"] == 10
        assert f["column"] == 5
        assert f["severity"] == "error"
        assert f["code"] == "arg-type"
        assert "incompatible type" in f["message"]

    def test_error_without_column(self):
        text = "src/foo.py:42: error: missing return statement  [return]\n"
        out = _parse_mypy_output(text)
        assert out[0]["column"] == 0
        assert out[0]["code"] == "return"

    def test_note_without_code(self):
        text = "src/foo.py:5:1: note: Use Optional[X] for arguments that default to None\n"
        out = _parse_mypy_output(text)
        assert out[0]["severity"] == "note"
        assert out[0]["code"] == ""

    def test_warning(self):
        text = "src/foo.py:1:1: warning: unused 'type: ignore' comment\n"
        out = _parse_mypy_output(text)
        assert out[0]["severity"] == "warning"

    def test_summary_lines_skipped(self):
        text = (
            "src/foo.py:1:1: error: bad  [bad]\n"
            "Found 1 error in 1 file (checked 1 source file)\n"
            "\n"
            "Success: no issues found in 0 source files\n"
        )
        out = _parse_mypy_output(text)
        assert len(out) == 1

    def test_empty_input(self):
        assert _parse_mypy_output("") == []

    def test_multiple_findings(self):
        text = (
            "a.py:1:1: error: bad  [bad]\n"
            "b.py:2:2: error: also bad  [also-bad]\n"
        )
        assert len(_parse_mypy_output(text)) == 2


# ===========================================================================
# Path allowlist
# ===========================================================================
class TestPathAllowlist:
    def test_within(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        assert _is_within_any(tmp_path.resolve(), roots) is True

    def test_outside(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        outside = (tmp_path.parent / "elsewhere").resolve()
        assert _is_within_any(outside, roots) is False


# ===========================================================================
# execute()
# ===========================================================================
class TestExecute:
    def test_clean_file_returns_no_findings(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("x: int = 1\n")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        ok = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck._locate_mypy",
            return_value=("mypy",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            return_value=ok,
        ):
            result = _run(MypyTypecheckTool().execute(
                {"path": str(f)}, ctx,
            ))
        assert result.output["findings_count"] == 0
        assert result.output["exit_code"] == 0
        assert result.output["truncated"] is False

    def test_findings_parsed(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("x: int = 'str'\n")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        bad = subprocess.CompletedProcess(
            args=[], returncode=1,
            stdout=f"{f}:1:5: error: Incompatible types in assignment "
                   "(expression has type \"str\", variable has type \"int\")  [assignment]\n",
            stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck._locate_mypy",
            return_value=("mypy",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            return_value=bad,
        ):
            result = _run(MypyTypecheckTool().execute({"path": str(f)}, ctx))
        assert result.output["findings_count"] == 1
        assert result.output["exit_code"] == 1
        assert result.output["findings"][0]["code"] == "assignment"

    def test_max_findings_truncation(self, tmp_path):
        f = tmp_path / "many.py"
        f.write_text("x = 1\n")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        stdout = "".join(
            f"{f}:{i}:1: error: bad  [bad]\n" for i in range(1, 11)
        )
        result_proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=stdout, stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck._locate_mypy",
            return_value=("mypy",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            return_value=result_proc,
        ):
            result = _run(MypyTypecheckTool().execute(
                {"path": str(f), "max_findings": 3}, ctx,
            ))
        assert result.output["findings_count"] == 3
        assert result.output["truncated"] is True

    def test_missing_allowed_paths_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s", constraints={},
        )
        with pytest.raises(MypyTypecheckError, match="allowed_paths"):
            _run(MypyTypecheckTool().execute({"path": str(tmp_path)}, ctx))

    def test_outside_allowed_blocked(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        target = tmp_path / "elsewhere"
        target.mkdir()
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(allowed)]},
        )
        with pytest.raises(MypyTypecheckError, match="outside"):
            _run(MypyTypecheckTool().execute({"path": str(target)}, ctx))

    def test_nonexistent_path_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(MypyTypecheckError, match="does not exist"):
            _run(MypyTypecheckTool().execute(
                {"path": str(tmp_path / "nope")}, ctx,
            ))

    def test_config_file_outside_allowed_refuses(self, tmp_path, monkeypatch):
        f = tmp_path / "x.py"
        f.write_text("hi")
        # Config file lives in a different place
        outside_cfg = tmp_path.parent / "mypy.ini"
        outside_cfg.write_text("[mypy]\n")
        try:
            ctx = ToolContext(
                instance_id="i", agent_dna="d" * 12, role="r", genre="g",
                session_id="s",
                constraints={"allowed_paths": [str(tmp_path)]},
            )
            with pytest.raises(MypyTypecheckError, match="config_file"):
                _run(MypyTypecheckTool().execute(
                    {"path": str(f), "config_file": str(outside_cfg)}, ctx,
                ))
        finally:
            outside_cfg.unlink(missing_ok=True)

    def test_config_file_nonexistent_refuses(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(MypyTypecheckError, match="config_file"):
            _run(MypyTypecheckTool().execute({
                "path": str(f),
                "config_file": str(tmp_path / "nope.ini"),
            }, ctx))

    def test_strict_flag_added(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("x = 1")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        captured: dict = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return ok

        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck._locate_mypy",
            return_value=("mypy",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            side_effect=fake_run,
        ):
            _run(MypyTypecheckTool().execute(
                {"path": str(f), "strict": True}, ctx,
            ))
        assert "--strict" in captured["argv"]

    def test_no_incremental_always_added(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("x = 1")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        captured: dict = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return ok

        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck._locate_mypy",
            return_value=("mypy",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            side_effect=fake_run,
        ):
            _run(MypyTypecheckTool().execute({"path": str(f)}, ctx))
        # --no-incremental honors the read_only contract
        assert "--no-incremental" in captured["argv"]

    def test_timeout_refuses(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck._locate_mypy",
            return_value=("mypy",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["mypy"], timeout=60),
        ):
            with pytest.raises(MypyTypecheckError, match="timed out"):
                _run(MypyTypecheckTool().execute({"path": str(f)}, ctx))

    def test_mypy_not_installed_refuses(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck._locate_mypy",
            return_value=None,
        ):
            with pytest.raises(MypyNotInstalledError):
                _run(MypyTypecheckTool().execute({"path": str(f)}, ctx))

    def test_mypy_hard_error_refuses(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        # Exit code 2 = command-line / config error
        bad = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="error: invalid config\n",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck._locate_mypy",
            return_value=("mypy",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            return_value=bad,
        ):
            with pytest.raises(MypyTypecheckError, match="exited with code 2"):
                _run(MypyTypecheckTool().execute({"path": str(f)}, ctx))

    def test_metadata_records_invocation(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck._locate_mypy",
            return_value=("mypy",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.mypy_typecheck.subprocess.run",
            return_value=ok,
        ):
            result = _run(MypyTypecheckTool().execute({"path": str(f)}, ctx))
        assert result.metadata["mypy_invocation"] == ["mypy"]
        assert result.metadata["max_findings"] == DEFAULT_MAX_FINDINGS
        assert result.metadata["strict"] is False


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_tool_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins

        registry = ToolRegistry()
        register_builtins(registry)
        tool = registry.get("mypy_typecheck", "1")
        assert tool is not None
        assert tool.side_effects == "read_only"

    def test_catalog_entry_present(self):
        import yaml
        catalog_path = (
            Path(__file__).parent.parent.parent
            / "config" / "tool_catalog.yaml"
        )
        with open(catalog_path) as f:
            catalog = yaml.safe_load(f)
        entry = catalog["tools"]["mypy_typecheck.v1"]
        assert entry["side_effects"] == "read_only"
        assert "required_initiative_level" not in entry
