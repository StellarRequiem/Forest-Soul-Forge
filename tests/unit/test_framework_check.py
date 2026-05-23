"""Tests for ADR-0085 Phase B — framework_check.v1 builtin tool.

Coverage:
- Argument validation (framework_id required, no path traversal,
  type checks on optional args)
- Missing framework yaml → error in output, no crash
- Invalid framework yaml → error in output, no crash
- required_file rule: pass when all present; fail when any missing;
  skipped when no paths
- forbidden_pattern rule: pass when clean; fail when matched;
  skipped on regex error / no scan_paths
- required_attestation rule (via chain): pass when tag found in
  window; fail when absent; skipped on missing tag / max_age
- audit_event_required rule: pass when event found in window;
  fail when absent
- Unknown rule kind → skipped:unknown_kind
- Control aggregation: all-pass → controls_passing;
  all-fail → controls_failing; mixed → controls_partial
- control_ids filter restricts evaluation
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.framework_check import (
    FrameworkCheckTool,
)


def _ctx():
    return ToolContext(
        instance_id="t", agent_dna="a" * 12,
        role="compliance_scanner", genre="guardian",
        session_id=None,
    )


def _run(args):
    return asyncio.run(FrameworkCheckTool().execute(args, _ctx()))


def _write_framework(
    framework_dir: Path,
    framework_id: str,
    controls: list[dict],
    *,
    name: str = "Test Framework",
    version: str = "test",
) -> Path:
    framework_dir.mkdir(parents=True, exist_ok=True)
    path = framework_dir / f"{framework_id}.yaml"
    path.write_text(yaml.safe_dump({
        "framework_id":   framework_id,
        "framework_name": name,
        "version":        version,
        "controls":       controls,
    }), encoding="utf-8")
    return path


def _write_chain(
    path: Path,
    entries: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TestValidation:
    def test_framework_id_required(self):
        with pytest.raises(ToolValidationError, match="framework_id"):
            FrameworkCheckTool().validate({})

    def test_framework_id_must_be_string(self):
        with pytest.raises(ToolValidationError, match="framework_id"):
            FrameworkCheckTool().validate({"framework_id": 42})

    def test_framework_id_no_path_traversal(self):
        with pytest.raises(ToolValidationError, match="alphanumeric"):
            FrameworkCheckTool().validate({
                "framework_id": "../etc/passwd",
            })

    def test_framework_id_no_slashes(self):
        with pytest.raises(ToolValidationError, match="alphanumeric"):
            FrameworkCheckTool().validate({
                "framework_id": "soc2/extra",
            })

    def test_control_ids_must_be_list_of_strings(self):
        with pytest.raises(ToolValidationError, match="control_ids"):
            FrameworkCheckTool().validate({
                "framework_id": "soc2",
                "control_ids": "CC6.1",
            })
        with pytest.raises(ToolValidationError, match="control_ids"):
            FrameworkCheckTool().validate({
                "framework_id": "soc2",
                "control_ids": ["CC6.1", ""],
            })

    def test_framework_dir_must_be_string(self):
        with pytest.raises(ToolValidationError, match="framework_dir"):
            FrameworkCheckTool().validate({
                "framework_id": "soc2",
                "framework_dir": 5,
            })


class TestMissingOrInvalidFramework:
    def test_missing_yaml_reports_error(self, tmp_path):
        out = _run({
            "framework_id":   "nonexistent",
            "framework_dir":  str(tmp_path),
        })
        body = out.output
        assert body["controls_evaluated"] == 0
        assert any("not found" in e for e in body["errors"])

    def test_invalid_yaml_reports_error(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(
            "controls: [this is :: not valid yaml",
            encoding="utf-8",
        )
        out = _run({
            "framework_id":   "bad",
            "framework_dir":  str(tmp_path),
        })
        body = out.output
        assert body["controls_evaluated"] == 0
        assert any("yaml" in e.lower() for e in body["errors"])


class TestRequiredFile:
    def test_all_present_passes(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_text("present", encoding="utf-8")
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "required_file",
                "params":  {"paths": [str(target)]},
                "severity": "low",
            }],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        rs = out.output["rule_results"]
        assert len(rs) == 1
        assert rs[0]["verdict"] == "pass"
        assert out.output["controls_passing"] == 1

    def test_missing_fails(self, tmp_path):
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "required_file",
                "params":  {"paths": [str(tmp_path / "missing.txt")]},
                "severity": "high",
            }],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        rs = out.output["rule_results"]
        assert rs[0]["verdict"] == "fail"
        assert "missing" in rs[0]["detail"]
        assert out.output["controls_failing"] == 1

    def test_no_paths_skipped(self, tmp_path):
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "required_file",
                "params":  {},
                "severity": "low",
            }],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        assert out.output["rule_results"][0]["verdict"] == "skipped"


class TestForbiddenPattern:
    def test_clean_passes(self, tmp_path):
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        (scan_root / "clean.txt").write_text(
            "nothing to see here", encoding="utf-8",
        )
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "forbidden_pattern",
                "params":  {
                    "pattern":    r"SECRET_[A-Z_]+",
                    "scan_paths": [str(scan_root)],
                },
                "severity": "high",
            }],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        assert out.output["rule_results"][0]["verdict"] == "pass"

    def test_match_fails(self, tmp_path):
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        (scan_root / "leaky.txt").write_text(
            "API_KEY = '0123456789abcdef0123'", encoding="utf-8",
        )
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "forbidden_pattern",
                "params":  {
                    "pattern": (
                        r"(?i)(api_key|secret_key|password)\s*=\s*"
                        r"['\"][a-zA-Z0-9_-]{16,}"
                    ),
                    "scan_paths": [str(scan_root)],
                },
                "severity": "high",
            }],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        rs = out.output["rule_results"]
        assert rs[0]["verdict"] == "fail"
        assert "matched_in" in rs[0]["detail"]

    def test_regex_error_skipped(self, tmp_path):
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "forbidden_pattern",
                "params":  {
                    "pattern":    "[",  # invalid regex
                    "scan_paths": [str(tmp_path)],
                },
                "severity": "low",
            }],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        rs = out.output["rule_results"]
        assert rs[0]["verdict"] == "skipped"
        assert "regex_error" in rs[0]["detail"]


class TestAttestationViaChain:
    def test_tag_found_in_window_passes(self, tmp_path):
        chain = tmp_path / "audit_chain.jsonl"
        now = time.time()
        _write_chain(chain, [{
            "event_type": "memory_written",
            "ts":         now - 60,
            "payload":    {"tags": ["audit_chain_verified"]},
        }])
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "required_attestation",
                "params":  {
                    "tag":           "audit_chain_verified",
                    "max_age_hours": 24,
                },
                "severity": "high",
            }],
        }])
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
        })
        rs = out.output["rule_results"]
        assert rs[0]["verdict"] == "pass"

    def test_tag_outside_window_fails(self, tmp_path):
        chain = tmp_path / "audit_chain.jsonl"
        old = time.time() - 48 * 3600
        _write_chain(chain, [{
            "event_type": "memory_written",
            "ts":         old,
            "payload":    {"tags": ["audit_chain_verified"]},
        }])
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "required_attestation",
                "params":  {
                    "tag":           "audit_chain_verified",
                    "max_age_hours": 24,
                },
                "severity": "high",
            }],
        }])
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
        })
        rs = out.output["rule_results"]
        assert rs[0]["verdict"] == "fail"

    def test_missing_tag_skipped(self, tmp_path):
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "required_attestation",
                "params":  {"max_age_hours": 24},
                "severity": "high",
            }],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        rs = out.output["rule_results"]
        assert rs[0]["verdict"] == "skipped"


class TestAuditEventRequired:
    def test_event_found_passes(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        now = time.time()
        _write_chain(chain, [{
            "event_type": "detection_engine_scan_complete",
            "ts":         now - 100,
        }])
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "audit_event_required",
                "params":  {
                    "event_type":    "detection_engine_scan_complete",
                    "max_age_hours": 24,
                },
                "severity": "medium",
            }],
        }])
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
        })
        rs = out.output["rule_results"]
        assert rs[0]["verdict"] == "pass"

    def test_event_absent_fails(self, tmp_path):
        chain = tmp_path / "chain.jsonl"
        _write_chain(chain, [{
            "event_type": "some_other_event",
            "ts":         time.time(),
        }])
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "audit_event_required",
                "params":  {
                    "event_type":    "detection_engine_scan_complete",
                    "max_age_hours": 24,
                },
                "severity": "medium",
            }],
        }])
        out = _run({
            "framework_id":     "fwk",
            "framework_dir":    str(tmp_path),
            "audit_chain_path": str(chain),
        })
        rs = out.output["rule_results"]
        assert rs[0]["verdict"] == "fail"


class TestUnknownKind:
    def test_unknown_kind_skipped(self, tmp_path):
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [{
                "rule_id": "r1",
                "kind":    "future_kind_not_implemented",
                "params":  {},
                "severity": "low",
            }],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        rs = out.output["rule_results"]
        assert rs[0]["verdict"] == "skipped"
        assert "unknown_kind" in rs[0]["detail"]


class TestControlAggregation:
    def test_all_pass_then_passing(self, tmp_path):
        f1 = tmp_path / "f1.txt"
        f1.write_text("ok", encoding="utf-8")
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [
                {"rule_id": "r1", "kind": "required_file",
                 "params": {"paths": [str(f1)]}, "severity": "low"},
                {"rule_id": "r2", "kind": "required_file",
                 "params": {"paths": [str(f1)]}, "severity": "low"},
            ],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        assert out.output["controls_passing"] == 1
        assert out.output["controls_failing"] == 0
        assert out.output["controls_partial"] == 0

    def test_all_fail_then_failing(self, tmp_path):
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [
                {"rule_id": "r1", "kind": "required_file",
                 "params": {"paths": ["/nonexistent/a"]}, "severity": "low"},
                {"rule_id": "r2", "kind": "required_file",
                 "params": {"paths": ["/nonexistent/b"]}, "severity": "low"},
            ],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        assert out.output["controls_failing"] == 1

    def test_mixed_then_partial(self, tmp_path):
        ok = tmp_path / "ok.txt"
        ok.write_text("x", encoding="utf-8")
        _write_framework(tmp_path, "fwk", [{
            "id": "C1",
            "rules": [
                {"rule_id": "r1", "kind": "required_file",
                 "params": {"paths": [str(ok)]}, "severity": "low"},
                {"rule_id": "r2", "kind": "required_file",
                 "params": {"paths": ["/nonexistent/x"]}, "severity": "low"},
            ],
        }])
        out = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        assert out.output["controls_partial"] == 1


class TestControlFilter:
    def test_filter_restricts(self, tmp_path):
        ok = tmp_path / "ok.txt"
        ok.write_text("x", encoding="utf-8")
        _write_framework(tmp_path, "fwk", [
            {"id": "C1", "rules": [{
                "rule_id": "r1", "kind": "required_file",
                "params":  {"paths": [str(ok)]}, "severity": "low",
            }]},
            {"id": "C2", "rules": [{
                "rule_id": "r2", "kind": "required_file",
                "params":  {"paths": ["/missing"]}, "severity": "low",
            }]},
        ])
        # Without filter: 1 passing + 1 failing
        out_all = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
        })
        assert out_all.output["controls_evaluated"] == 2

        # With filter restricting to C1: 1 passing
        out_filtered = _run({
            "framework_id":  "fwk",
            "framework_dir": str(tmp_path),
            "control_ids":   ["C1"],
        })
        assert out_filtered.output["controls_evaluated"] == 1
        assert out_filtered.output["controls_passing"] == 1
        assert out_filtered.output["controls_failing"] == 0


class TestRealSoc2Smoke:
    """Smoke test against the real SOC2 seed framework yaml.

    Doesn't assert specific verdicts (depends on live system
    state); just confirms the real yaml loads + evaluates without
    error. The rule_results count must equal the sum of rules
    across controls.
    """

    def test_soc2_loads_and_evaluates(self, tmp_path):
        repo_root = Path(__file__).resolve().parents[2]
        framework_dir = repo_root / "config" / "compliance_frameworks"
        out = _run({
            "framework_id":     "soc2",
            "framework_dir":    str(framework_dir),
            # Point chain at a tmpfile so we don't pull the live one
            "audit_chain_path": str(tmp_path / "empty_chain.jsonl"),
        })
        body = out.output
        assert body["framework_id"] == "soc2"
        assert body["framework_name"]
        assert body["controls_evaluated"] >= 1
        # No load errors expected.
        assert not any(
            "yaml" in e.lower() or "not found" in e.lower()
            for e in body["errors"]
        )
