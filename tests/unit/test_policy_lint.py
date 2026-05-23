"""Tests for ADR-0085 Phase C — policy_lint.v1 builtin tool.

Coverage:
- Argument validation
- Missing framework yaml → error in output, no crash
- Empty / absent lint_rules → empty findings, no error
- yaml_key_required: pass when present; finding when missing;
  finding when value differs from expected_value;
  finding when value_pattern doesn't match
- yaml_key_forbidden: pass when absent; finding when present
- file_max_age_days: pass when recent; finding when too old
- file_pattern gating: rule skipped if path doesn't match
- rule_ids filter restricts evaluation
- Real-soc2 smoke: lint_rules section parses + linter runs on
  a fixture config without crashing
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.policy_lint import PolicyLintTool


def _ctx():
    return ToolContext(
        instance_id="t", agent_dna="a" * 12,
        role="policy_enforcer", genre="actuator",
        session_id=None,
    )


def _run(args):
    return asyncio.run(PolicyLintTool().execute(args, _ctx()))


def _write_framework(
    framework_dir: Path,
    framework_id: str,
    lint_rules: list[dict],
    *,
    controls: list[dict] | None = None,
) -> Path:
    framework_dir.mkdir(parents=True, exist_ok=True)
    path = framework_dir / f"{framework_id}.yaml"
    body = {
        "framework_id":   framework_id,
        "framework_name": f"{framework_id} test",
        "version":        "test",
        "controls":       controls or [],
        "lint_rules":     lint_rules,
    }
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def _write_yaml(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


class TestValidation:
    def test_framework_id_required(self):
        with pytest.raises(ToolValidationError, match="framework_id"):
            PolicyLintTool().validate({"target_paths": ["a"]})

    def test_target_paths_required(self):
        with pytest.raises(ToolValidationError, match="target_paths"):
            PolicyLintTool().validate({"framework_id": "soc2"})

    def test_target_paths_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="target_paths"):
            PolicyLintTool().validate({
                "framework_id": "soc2", "target_paths": [],
            })

    def test_framework_id_no_path_traversal(self):
        with pytest.raises(ToolValidationError, match="alphanumeric"):
            PolicyLintTool().validate({
                "framework_id": "../etc/passwd",
                "target_paths": ["a"],
            })

    def test_target_paths_cap(self):
        with pytest.raises(ToolValidationError, match="capped"):
            PolicyLintTool().validate({
                "framework_id": "soc2",
                "target_paths": [f"p{i}" for i in range(101)],
            })

    def test_rule_ids_must_be_list_of_strings(self):
        with pytest.raises(ToolValidationError, match="rule_ids"):
            PolicyLintTool().validate({
                "framework_id": "soc2",
                "target_paths": ["a"],
                "rule_ids":     "single",
            })


class TestMissingOrInvalidFramework:
    def test_missing_yaml_reports_error(self, tmp_path):
        target = tmp_path / "any.yaml"
        target.write_text("{}", encoding="utf-8")
        out = _run({
            "framework_id":  "nonexistent",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        body = out.output
        assert body["findings"] == []
        assert any("not found" in e for e in body["errors"])

    def test_invalid_yaml_reports_error(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(
            "controls: [this is :: bad", encoding="utf-8",
        )
        target = tmp_path / "any.yaml"
        target.write_text("{}", encoding="utf-8")
        out = _run({
            "framework_id":  "bad",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        assert any("yaml" in e.lower() for e in out.output["errors"])


class TestEmptyLintRules:
    def test_no_lint_rules_returns_empty_findings(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[])
        target = _write_yaml(tmp_path / "t.yaml", {"foo": "bar"})
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        assert out.output["findings"] == []
        assert out.output["files_evaluated"] == 1


class TestYamlKeyRequired:
    def test_present_passes(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[{
            "rule_id": "r1",
            "kind":    "yaml_key_required",
            "params":  {"key": "auth.enabled"},
            "severity": "low",
        }])
        target = _write_yaml(tmp_path / "t.yaml", {
            "auth": {"enabled": True},
        })
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        assert out.output["findings"] == []

    def test_missing_finds(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[{
            "rule_id": "r1",
            "kind":    "yaml_key_required",
            "params":  {"key": "auth.enabled"},
            "severity": "high",
            "remediation": "Add auth.enabled.",
        }])
        target = _write_yaml(tmp_path / "t.yaml", {"foo": "bar"})
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        findings = out.output["findings"]
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "r1"
        assert findings[0]["severity"] == "high"
        assert "missing" in findings[0]["message"]
        assert "Add" in findings[0]["proposal"]

    def test_expected_value_mismatch_finds(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[{
            "rule_id": "r1",
            "kind":    "yaml_key_required",
            "params":  {
                "key":             "auth.enabled",
                "expected_value":  True,
            },
            "severity": "high",
        }])
        target = _write_yaml(tmp_path / "t.yaml", {
            "auth": {"enabled": False},
        })
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        findings = out.output["findings"]
        assert len(findings) == 1
        assert "expected" in findings[0]["message"]

    def test_value_pattern_mismatch_finds(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[{
            "rule_id": "r1",
            "kind":    "yaml_key_required",
            "params":  {
                "key":           "version",
                "value_pattern": r"^\d+\.\d+\.\d+$",
            },
            "severity": "medium",
        }])
        target = _write_yaml(tmp_path / "t.yaml", {"version": "broken"})
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        findings = out.output["findings"]
        assert len(findings) == 1
        assert "does not match" in findings[0]["message"]


class TestYamlKeyForbidden:
    def test_absent_passes(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[{
            "rule_id": "r1",
            "kind":    "yaml_key_forbidden",
            "params":  {"key": "auth.anonymous"},
            "severity": "high",
        }])
        target = _write_yaml(tmp_path / "t.yaml", {
            "auth": {"enabled": True},
        })
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        assert out.output["findings"] == []

    def test_present_finds(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[{
            "rule_id": "r1",
            "kind":    "yaml_key_forbidden",
            "params":  {"key": "auth.anonymous"},
            "severity": "high",
            "remediation": "Remove auth.anonymous.",
        }])
        target = _write_yaml(tmp_path / "t.yaml", {
            "auth": {"anonymous": True},
        })
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        findings = out.output["findings"]
        assert len(findings) == 1
        assert "forbidden" in findings[0]["message"]
        assert "Remove" in findings[0]["proposal"]


class TestFileMaxAge:
    def test_recent_passes(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[{
            "rule_id": "r1",
            "kind":    "file_max_age_days",
            "params":  {"max_days": 90},
            "severity": "low",
        }])
        target = tmp_path / "fresh.txt"
        target.write_text("recent", encoding="utf-8")
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        assert out.output["findings"] == []

    def test_too_old_finds(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[{
            "rule_id": "r1",
            "kind":    "file_max_age_days",
            "params":  {"max_days": 30},
            "severity": "low",
            "remediation": "Rotate the file.",
        }])
        target = tmp_path / "stale.txt"
        target.write_text("old", encoding="utf-8")
        # backdate by 120 days
        old_ts = time.time() - 120 * 86400
        os.utime(target, (old_ts, old_ts))
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        findings = out.output["findings"]
        assert len(findings) == 1
        assert "days old" in findings[0]["message"]
        assert "Rotate" in findings[0]["proposal"]


class TestFilePatternGating:
    def test_rule_skipped_when_path_doesnt_match(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[{
            "rule_id": "r1",
            "kind":    "yaml_key_required",
            "params":  {
                "key":          "auth.enabled",
                "file_pattern": "settings\\.local\\.json$",
            },
            "severity": "low",
        }])
        # Target is a generic.yaml — should NOT match
        # the file_pattern, so rule is skipped + no finding.
        target = _write_yaml(tmp_path / "generic.yaml", {"foo": "bar"})
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        assert out.output["findings"] == []


class TestRuleIdsFilter:
    def test_filter_restricts(self, tmp_path):
        _write_framework(tmp_path, "fwk", lint_rules=[
            {"rule_id": "r1", "kind": "yaml_key_required",
             "params": {"key": "missing_one"}, "severity": "low"},
            {"rule_id": "r2", "kind": "yaml_key_required",
             "params": {"key": "missing_two"}, "severity": "low"},
        ])
        target = _write_yaml(tmp_path / "t.yaml", {})
        # Without filter: 2 findings
        out_all = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
        })
        assert len(out_all.output["findings"]) == 2

        # Filtered to r1: 1 finding
        out_filtered = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "target_paths":  [str(target)],
            "rule_ids":      ["r1"],
        })
        findings = out_filtered.output["findings"]
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "r1"


class TestRealSoc2Smoke:
    """Smoke test against the real SOC2 seed framework's lint_rules.

    Doesn't assert specific findings (depends on what the operator
    has in target files); just confirms the real yaml loads + the
    linter runs without crashing.
    """

    def test_soc2_loads_and_runs(self, tmp_path):
        repo_root = Path(__file__).resolve().parents[2]
        framework_dir = repo_root / "config" / "compliance_frameworks"
        # Provide a benign target so files_evaluated >= 1.
        target = _write_yaml(tmp_path / "innocuous.yaml", {
            "foo": "bar",
        })
        out = _run({
            "framework_id":  "soc2",
            "framework_dir": str(framework_dir),
            "target_paths":  [str(target)],
        })
        body = out.output
        assert body["framework_id"] == "soc2"
        assert body["files_evaluated"] >= 1
        assert not any(
            "yaml parse error" in e or "not found" in e
            for e in body["errors"]
        )
