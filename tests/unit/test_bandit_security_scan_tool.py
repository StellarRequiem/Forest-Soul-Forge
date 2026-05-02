"""Tests for bandit_security_scan.v1 (Phase G.1.A ninth programming primitive)."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.bandit_security_scan import (
    BANDIT_MAX_FINDINGS_HARD_CAP,
    DEFAULT_MAX_FINDINGS,
    DEFAULT_TIMEOUT_SECONDS,
    VALID_LEVELS,
    BanditNotInstalledError,
    BanditScanError,
    BanditSecurityScanTool,
    _confidence_to_flag,
    _is_within_any,
    _locate_bandit,
    _normalize_finding,
    _resolve_allowlist,
    _severity_to_flag,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    fp = tmp_path / "sample.py"
    fp.write_text("import pickle\npickle.loads(open('x').read())\n")
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
            BanditSecurityScanTool().validate({})

    def test_invalid_severity_rejected(self):
        with pytest.raises(ToolValidationError, match="severity_level"):
            BanditSecurityScanTool().validate(
                {"path": "/tmp", "severity_level": "extreme"},
            )

    def test_invalid_confidence_rejected(self):
        with pytest.raises(ToolValidationError, match="confidence_level"):
            BanditSecurityScanTool().validate(
                {"path": "/tmp", "confidence_level": "maybe"},
            )

    def test_invalid_skip_tests_rejected(self):
        with pytest.raises(ToolValidationError, match="skip_tests"):
            BanditSecurityScanTool().validate(
                {"path": "/tmp", "skip_tests": "B101"},   # string, not list
            )
        with pytest.raises(ToolValidationError, match="skip_tests"):
            BanditSecurityScanTool().validate(
                {"path": "/tmp", "skip_tests": ["bad-id"]},
            )
        with pytest.raises(ToolValidationError, match="skip_tests"):
            BanditSecurityScanTool().validate(
                {"path": "/tmp", "skip_tests": ["B12"]},   # too short
            )

    def test_max_findings_bounds(self):
        with pytest.raises(ToolValidationError, match="max_findings"):
            BanditSecurityScanTool().validate({"path": "/tmp", "max_findings": 0})
        with pytest.raises(ToolValidationError, match="max_findings"):
            BanditSecurityScanTool().validate({
                "path": "/tmp",
                "max_findings": BANDIT_MAX_FINDINGS_HARD_CAP + 1,
            })

    def test_timeout_bounds(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            BanditSecurityScanTool().validate({"path": "/tmp", "timeout_seconds": 0})
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            BanditSecurityScanTool().validate({"path": "/tmp", "timeout_seconds": 601})

    def test_valid_minimal(self):
        BanditSecurityScanTool().validate({"path": "/tmp"})

    def test_valid_full(self):
        BanditSecurityScanTool().validate({
            "path": "/tmp/repo",
            "severity_level": "medium",
            "confidence_level": "high",
            "skip_tests": ["B101", "B102"],
            "max_findings": 200,
            "timeout_seconds": 120,
        })

    def test_valid_levels_constant(self):
        assert set(VALID_LEVELS) == {"low", "medium", "high"}


# ===========================================================================
# Helper functions
# ===========================================================================
class TestSeverityFlag:
    def test_severity_to_flag(self):
        assert _severity_to_flag("low") == "l"
        assert _severity_to_flag("medium") == "ll"
        assert _severity_to_flag("high") == "lll"

    def test_confidence_to_flag(self):
        assert _confidence_to_flag("low") == "i"
        assert _confidence_to_flag("medium") == "ii"
        assert _confidence_to_flag("high") == "iii"


class TestLocateBandit:
    def test_path_first(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.shutil.which",
            return_value="/usr/local/bin/bandit",
        ):
            assert _locate_bandit() == ("bandit",)

    def test_module_fallback(self):
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="1.7", stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.shutil.which",
            return_value=None,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            return_value=ok,
        ):
            assert _locate_bandit() == ("python3", "-m", "bandit")

    def test_neither_works(self):
        bad = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.shutil.which",
            return_value=None,
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            return_value=bad,
        ):
            assert _locate_bandit() is None


class TestNormalizeFinding:
    def test_full_finding(self):
        raw = {
            "test_id": "B301",
            "test_name": "pickle",
            "issue_severity": "MEDIUM",
            "issue_confidence": "HIGH",
            "filename": "src/x.py",
            "line_number": 5,
            "issue_text": "pickle is unsafe",
            "code": "pickle.loads(data)",
            "more_info": "https://bandit.readthedocs.io/...",
        }
        f = _normalize_finding(raw)
        assert f["test_id"] == "B301"
        assert f["severity"] == "MEDIUM"
        assert f["confidence"] == "HIGH"
        assert f["line"] == 5
        assert f["code_snippet"] == "pickle.loads(data)"

    def test_missing_fields_default(self):
        f = _normalize_finding({})
        assert f["severity"] == "LOW"
        assert f["confidence"] == "LOW"
        assert f["line"] == 0


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
    def test_clean_scan(self, env):
        ctx, fp = env
        ok = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"results": []}), stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            return_value=ok,
        ):
            result = _run(BanditSecurityScanTool().execute(
                {"path": str(fp)}, ctx,
            ))
        assert result.output["findings_count"] == 0

    def test_findings_parsed(self, env):
        ctx, fp = env
        payload = {
            "results": [{
                "test_id": "B301",
                "test_name": "pickle",
                "issue_severity": "MEDIUM",
                "issue_confidence": "HIGH",
                "filename": str(fp),
                "line_number": 2,
                "issue_text": "Pickle library appears to be in use",
                "code": "pickle.loads(...)",
                "more_info": "url",
            }]
        }
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=json.dumps(payload), stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            return_value=proc,
        ):
            result = _run(BanditSecurityScanTool().execute(
                {"path": str(fp)}, ctx,
            ))
        assert result.output["findings_count"] == 1
        assert result.output["findings"][0]["test_id"] == "B301"

    def test_truncation(self, env):
        ctx, fp = env
        payload = {
            "results": [
                {"test_id": f"B{100+i}", "test_name": "x",
                 "issue_severity": "LOW", "issue_confidence": "LOW",
                 "filename": "x", "line_number": i,
                 "issue_text": "", "code": "", "more_info": ""}
                for i in range(10)
            ]
        }
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=json.dumps(payload), stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            return_value=proc,
        ):
            result = _run(BanditSecurityScanTool().execute(
                {"path": str(fp), "max_findings": 3}, ctx,
            ))
        assert result.output["findings_count"] == 3
        assert result.output["truncated"] is True

    def test_severity_flag_added(self, env):
        ctx, fp = env
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"results":[]}', stderr="")
        captured = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return ok

        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            side_effect=fake_run,
        ):
            _run(BanditSecurityScanTool().execute(
                {"path": str(fp), "severity_level": "high"}, ctx,
            ))
        assert "-llll" not in captured["argv"]
        assert "-lll" in captured["argv"]

    def test_skip_tests_added(self, env):
        ctx, fp = env
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"results":[]}', stderr="")
        captured = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return ok

        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            side_effect=fake_run,
        ):
            _run(BanditSecurityScanTool().execute(
                {"path": str(fp), "skip_tests": ["B101", "B404"]}, ctx,
            ))
        assert "--skip" in captured["argv"]
        idx = captured["argv"].index("--skip")
        assert captured["argv"][idx + 1] == "B101,B404"

    def test_recursive_flag_for_directory(self, tmp_path):
        d = tmp_path / "src"
        d.mkdir()
        (d / "x.py").write_text("hi")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"results":[]}', stderr="")
        captured = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return ok

        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            side_effect=fake_run,
        ):
            _run(BanditSecurityScanTool().execute(
                {"path": str(d)}, ctx,
            ))
        assert "-r" in captured["argv"]

    def test_missing_allowed_paths_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s", constraints={},
        )
        f = tmp_path / "x.py"
        f.write_text("hi")
        with pytest.raises(BanditScanError, match="allowed_paths"):
            _run(BanditSecurityScanTool().execute({"path": str(f)}, ctx))

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
        with pytest.raises(BanditScanError, match="outside"):
            _run(BanditSecurityScanTool().execute({"path": str(outside)}, ctx))

    def test_nonexistent_path(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(BanditScanError, match="does not exist"):
            _run(BanditSecurityScanTool().execute(
                {"path": str(tmp_path / "nope.py")}, ctx,
            ))

    def test_timeout_refuses(self, env):
        ctx, fp = env
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["bandit"], timeout=60),
        ):
            with pytest.raises(BanditScanError, match="timed out"):
                _run(BanditSecurityScanTool().execute({"path": str(fp)}, ctx))

    def test_bandit_not_installed(self, env):
        ctx, fp = env
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=None,
        ):
            with pytest.raises(BanditNotInstalledError):
                _run(BanditSecurityScanTool().execute({"path": str(fp)}, ctx))

    def test_hard_error_refuses(self, env):
        ctx, fp = env
        bad = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="error\n",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            return_value=bad,
        ):
            with pytest.raises(BanditScanError, match="exited with code 2"):
                _run(BanditSecurityScanTool().execute({"path": str(fp)}, ctx))

    def test_unparseable_json_refuses(self, env):
        ctx, fp = env
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not-json", stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            return_value=proc,
        ):
            with pytest.raises(BanditScanError, match="unparseable"):
                _run(BanditSecurityScanTool().execute({"path": str(fp)}, ctx))

    def test_metadata_records(self, env):
        ctx, fp = env
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"results":[]}', stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan._locate_bandit",
            return_value=("bandit",),
        ), mock.patch(
            "forest_soul_forge.tools.builtin.bandit_security_scan.subprocess.run",
            return_value=ok,
        ):
            result = _run(BanditSecurityScanTool().execute(
                {"path": str(fp)}, ctx,
            ))
        assert result.metadata["bandit_invocation"] == ["bandit"]
        assert result.metadata["max_findings"] == DEFAULT_MAX_FINDINGS
        assert result.metadata["severity_level"] == "low"


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_tool_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins

        registry = ToolRegistry()
        register_builtins(registry)
        tool = registry.get("bandit_security_scan", "1")
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
        entry = catalog["tools"]["bandit_security_scan.v1"]
        assert entry["side_effects"] == "read_only"
        assert "required_initiative_level" not in entry
