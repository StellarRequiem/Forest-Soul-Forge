"""Tests for semgrep_scan.v1 (Phase G.1.A seventh programming primitive)."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.semgrep_scan import (
    DEFAULT_MAX_FINDINGS,
    DEFAULT_TIMEOUT_SECONDS,
    SEMGREP_MAX_FINDINGS_HARD_CAP,
    VALID_SEVERITIES,
    SemgrepNotInstalledError,
    SemgrepScanError,
    SemgrepScanTool,
    _is_within_any,
    _locate_semgrep,
    _normalize_finding,
    _resolve_allowlist,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    fp = tmp_path / "sample.py"
    fp.write_text("import os\nos.system(input())\n", encoding="utf-8")
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
            SemgrepScanTool().validate({"config": "auto"})

    def test_missing_config_rejected(self):
        with pytest.raises(ToolValidationError, match="config"):
            SemgrepScanTool().validate({"path": "/tmp"})

    def test_empty_config_rejected(self):
        with pytest.raises(ToolValidationError, match="config"):
            SemgrepScanTool().validate({"path": "/tmp", "config": " "})

    def test_dash_prefixed_config_rejected(self):
        with pytest.raises(ToolValidationError, match="must not start with"):
            SemgrepScanTool().validate({"path": "/tmp", "config": "-rf"})

    def test_invalid_max_findings(self):
        with pytest.raises(ToolValidationError, match="max_findings"):
            SemgrepScanTool().validate(
                {"path": "/tmp", "config": "auto", "max_findings": 0},
            )
        with pytest.raises(ToolValidationError, match="max_findings"):
            SemgrepScanTool().validate({
                "path": "/tmp", "config": "auto",
                "max_findings": SEMGREP_MAX_FINDINGS_HARD_CAP + 1,
            })

    def test_invalid_timeout(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            SemgrepScanTool().validate(
                {"path": "/tmp", "config": "auto", "timeout_seconds": 0},
            )
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            SemgrepScanTool().validate(
                {"path": "/tmp", "config": "auto", "timeout_seconds": 601},
            )

    def test_invalid_severity_filter(self):
        with pytest.raises(ToolValidationError, match="severity_filter"):
            SemgrepScanTool().validate({
                "path": "/tmp", "config": "auto",
                "severity_filter": "ERROR",   # not a list
            })
        with pytest.raises(ToolValidationError, match="severity_filter"):
            SemgrepScanTool().validate({
                "path": "/tmp", "config": "auto",
                "severity_filter": ["BOGUS"],
            })

    def test_valid_minimal(self):
        SemgrepScanTool().validate({"path": "/tmp", "config": "auto"})

    def test_valid_full(self):
        SemgrepScanTool().validate({
            "path": "/tmp/repo",
            "config": "p/security-audit",
            "max_findings": 100,
            "severity_filter": ["ERROR", "WARNING"],
            "timeout_seconds": 120,
        })

    def test_valid_severities_constant(self):
        assert set(VALID_SEVERITIES) == {"ERROR", "WARNING", "INFO"}


# ===========================================================================
# _locate_semgrep
# ===========================================================================
class TestLocateSemgrep:
    def test_path_first(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.shutil.which",
            return_value="/usr/local/bin/semgrep",
        ):
            assert _locate_semgrep() == ("semgrep",)

    def test_module_fallback(self):
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="1.0", stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.shutil.which",
            return_value=None,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            return_value=ok,
        ):
            assert _locate_semgrep() == ("python3", "-m", "semgrep")

    def test_neither_works(self):
        bad = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.shutil.which",
            return_value=None,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            return_value=bad,
        ):
            assert _locate_semgrep() is None


# ===========================================================================
# _normalize_finding
# ===========================================================================
class TestNormalizeFinding:
    def test_full_finding(self):
        raw = {
            "check_id": "rules.dangerous-system",
            "path": "src/x.py",
            "start": {"line": 10, "col": 5},
            "end":   {"line": 10, "col": 25},
            "extra": {
                "severity": "ERROR",
                "message": "do not call os.system on user input",
                "lines": "os.system(input())",
            },
        }
        f = _normalize_finding(raw)
        assert f["rule_id"] == "rules.dangerous-system"
        assert f["severity"] == "ERROR"
        assert f["start_line"] == 10
        assert f["end_line"] == 10
        assert f["start_column"] == 5
        assert f["end_column"] == 25
        assert "os.system" in f["code_snippet"]

    def test_missing_extra_defaults(self):
        raw = {"check_id": "x", "path": "y.py", "start": {"line": 1, "col": 1}, "end": {"line": 1, "col": 2}}
        f = _normalize_finding(raw)
        assert f["severity"] == "INFO"
        assert f["message"] == ""
        assert f["code_snippet"] == ""

    def test_missing_positions_default_to_zero(self):
        raw = {"check_id": "x", "path": "y.py"}
        f = _normalize_finding(raw)
        assert f["start_line"] == 0
        assert f["end_line"] == 0


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
    def test_clean_scan_no_findings(self, env):
        ctx, fp = env
        ok = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"results": []}),
            stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan._locate_semgrep",
            return_value=("semgrep",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            return_value=ok,
        ):
            result = _run(SemgrepScanTool().execute(
                {"path": str(fp), "config": "auto"}, ctx,
            ))
        assert result.output["findings_count"] == 0
        assert result.output["truncated"] is False

    def test_findings_parsed(self, env):
        ctx, fp = env
        payload = {
            "results": [
                {
                    "check_id": "rules.x",
                    "path": str(fp),
                    "start": {"line": 2, "col": 1},
                    "end":   {"line": 2, "col": 19},
                    "extra": {"severity": "ERROR", "message": "bad", "lines": "os.system(input())"},
                }
            ]
        }
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=json.dumps(payload), stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan._locate_semgrep",
            return_value=("semgrep",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            return_value=proc,
        ):
            result = _run(SemgrepScanTool().execute(
                {"path": str(fp), "config": "auto"}, ctx,
            ))
        assert result.output["findings_count"] == 1
        assert result.output["findings"][0]["rule_id"] == "rules.x"

    def test_severity_filter_narrows(self, env):
        ctx, fp = env
        payload = {
            "results": [
                {"check_id": "a", "path": "x", "start": {"line": 1}, "end": {"line": 1},
                 "extra": {"severity": "ERROR", "message": "", "lines": ""}},
                {"check_id": "b", "path": "x", "start": {"line": 2}, "end": {"line": 2},
                 "extra": {"severity": "WARNING", "message": "", "lines": ""}},
                {"check_id": "c", "path": "x", "start": {"line": 3}, "end": {"line": 3},
                 "extra": {"severity": "INFO", "message": "", "lines": ""}},
            ]
        }
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=json.dumps(payload), stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan._locate_semgrep",
            return_value=("semgrep",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            return_value=proc,
        ):
            result = _run(SemgrepScanTool().execute({
                "path": str(fp), "config": "auto",
                "severity_filter": ["ERROR"],
            }, ctx))
        assert result.output["findings_count"] == 1
        assert result.output["findings"][0]["severity"] == "ERROR"

    def test_truncation(self, env):
        ctx, fp = env
        payload = {
            "results": [
                {"check_id": f"r{i}", "path": "x",
                 "start": {"line": i}, "end": {"line": i},
                 "extra": {"severity": "ERROR", "message": "", "lines": ""}}
                for i in range(1, 11)
            ]
        }
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=json.dumps(payload), stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan._locate_semgrep",
            return_value=("semgrep",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            return_value=proc,
        ):
            result = _run(SemgrepScanTool().execute({
                "path": str(fp), "config": "auto", "max_findings": 3,
            }, ctx))
        assert result.output["findings_count"] == 3
        assert result.output["truncated"] is True

    def test_yaml_config_must_exist(self, env):
        ctx, fp = env
        with pytest.raises(SemgrepScanError, match="config file"):
            _run(SemgrepScanTool().execute({
                "path": str(fp), "config": "/nope/rules.yaml",
            }, ctx))

    def test_yaml_config_outside_allowed_refuses(self, tmp_path):
        # rules.yaml outside allowed_paths
        outside = tmp_path.parent / "rules.yaml"
        outside.write_text("rules: []\n")
        try:
            target = tmp_path / "x.py"
            target.write_text("hi")
            ctx = ToolContext(
                instance_id="i", agent_dna="d" * 12, role="r", genre="g",
                session_id="s",
                constraints={"allowed_paths": [str(tmp_path)]},
            )
            with pytest.raises(SemgrepScanError, match="outside allowed_paths"):
                _run(SemgrepScanTool().execute({
                    "path": str(target), "config": str(outside),
                }, ctx))
        finally:
            outside.unlink(missing_ok=True)

    def test_missing_allowed_paths_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s", constraints={},
        )
        f = tmp_path / "x.py"
        f.write_text("hi")
        with pytest.raises(SemgrepScanError, match="allowed_paths"):
            _run(SemgrepScanTool().execute(
                {"path": str(f), "config": "auto"}, ctx,
            ))

    def test_outside_allowed_blocked(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "x.py"
        outside.write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(allowed)]},
        )
        with pytest.raises(SemgrepScanError, match="outside"):
            _run(SemgrepScanTool().execute(
                {"path": str(outside), "config": "auto"}, ctx,
            ))

    def test_nonexistent_path_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(SemgrepScanError, match="does not exist"):
            _run(SemgrepScanTool().execute({
                "path": str(tmp_path / "nope.py"), "config": "auto",
            }, ctx))

    def test_timeout_refuses(self, env):
        ctx, fp = env
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan._locate_semgrep",
            return_value=("semgrep",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["semgrep"], timeout=60),
        ):
            with pytest.raises(SemgrepScanError, match="timed out"):
                _run(SemgrepScanTool().execute(
                    {"path": str(fp), "config": "auto"}, ctx,
                ))

    def test_semgrep_not_installed(self, env):
        ctx, fp = env
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan._locate_semgrep",
            return_value=None,
        ):
            with pytest.raises(SemgrepNotInstalledError):
                _run(SemgrepScanTool().execute(
                    {"path": str(fp), "config": "auto"}, ctx,
                ))

    def test_hard_error_refuses(self, env):
        ctx, fp = env
        bad = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="",
            stderr="error: invalid config\n",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan._locate_semgrep",
            return_value=("semgrep",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            return_value=bad,
        ):
            with pytest.raises(SemgrepScanError, match="exited with code 2"):
                _run(SemgrepScanTool().execute(
                    {"path": str(fp), "config": "auto"}, ctx,
                ))

    def test_unparseable_json_refuses(self, env):
        ctx, fp = env
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not-json{{", stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan._locate_semgrep",
            return_value=("semgrep",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            return_value=proc,
        ):
            with pytest.raises(SemgrepScanError, match="unparseable"):
                _run(SemgrepScanTool().execute(
                    {"path": str(fp), "config": "auto"}, ctx,
                ))

    def test_metadata_records_invocation(self, env):
        ctx, fp = env
        ok = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"results": []}), stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan._locate_semgrep",
            return_value=("semgrep",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.semgrep_scan.subprocess.run",
            return_value=ok,
        ):
            result = _run(SemgrepScanTool().execute(
                {"path": str(fp), "config": "auto"}, ctx,
            ))
        assert result.metadata["semgrep_invocation"] == ["semgrep"]
        assert result.metadata["max_findings"] == DEFAULT_MAX_FINDINGS


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_tool_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins

        registry = ToolRegistry()
        register_builtins(registry)
        tool = registry.get("semgrep_scan", "1")
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
        entry = catalog["tools"]["semgrep_scan.v1"]
        assert entry["side_effects"] == "read_only"
        assert "required_initiative_level" not in entry
