"""Unit tests for ADR-0033 Phase B3 — posture + verify (high-tier).

Covers:
- posture_check.v1     (read-only OS posture probe)
- continuous_verify.v1 (pure-Python drift composer)
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin import (
    ContinuousVerifyTool,
    PostureCheckTool,
)


def _run(coro):
    return asyncio.run(coro)


def _ctx(**kw):
    base = dict(
        instance_id="i", agent_dna="x" * 12,
        role="guardian", genre="security_high", session_id="s",
    )
    base.update(kw)
    return ToolContext(**base)


# ============================================================================
# posture_check.v1
# ============================================================================
class TestPostureCheckValidation:
    def test_refuses_non_list_checks(self):
        with pytest.raises(ToolValidationError, match="checks"):
            PostureCheckTool().validate({"checks": "not a list"})

    def test_refuses_non_string_check_entry(self):
        with pytest.raises(ToolValidationError, match="checks"):
            PostureCheckTool().validate({"checks": [123]})

    def test_refuses_empty_check_name(self):
        with pytest.raises(ToolValidationError, match="checks"):
            PostureCheckTool().validate({"checks": [""]})

    def test_refuses_non_dict_severity_overrides(self):
        with pytest.raises(ToolValidationError, match="severity_overrides"):
            PostureCheckTool().validate({"severity_overrides": "bad"})

    def test_refuses_bad_severity_value(self):
        with pytest.raises(ToolValidationError, match="severity_overrides"):
            PostureCheckTool().validate({
                "severity_overrides": {"sip": "BOGUS"},
            })

    def test_accepts_valid_args(self):
        # Should not raise.
        PostureCheckTool().validate({
            "checks": ["sip", "filevault"],
            "severity_overrides": {"sip": "low", "filevault": "critical"},
        })


class TestPostureCheckPlatform:
    def test_unknown_platform_returns_no_checks(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Plan9",
        ):
            r = _run(PostureCheckTool().execute({}, _ctx()))
        assert r.output["platform"] == "unknown"
        assert r.output["checks"] == []
        assert r.output["overall_severity"] == "low"

    def test_darwin_runs_macos_probes(self):
        # Mock platform=darwin and shutil.which to make every probe
        # report 'binary not on PATH' — verifies the macOS probe set
        # is what gets attempted.
        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Darwin",
        ), mock.patch("shutil.which", return_value=None):
            r = _run(PostureCheckTool().execute({}, _ctx()))
        assert r.output["platform"] == "darwin"
        skipped_names = {s["name"] for s in r.output["checks_skipped"]}
        assert {"sip", "filevault", "gatekeeper", "firewall", "app_firewall"} <= skipped_names

    def test_linux_runs_linux_probes(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Linux",
        ), mock.patch("shutil.which", return_value=None):
            r = _run(PostureCheckTool().execute({}, _ctx()))
        assert r.output["platform"] == "linux"
        skipped_names = {s["name"] for s in r.output["checks_skipped"]}
        assert {"selinux", "apparmor", "ufw", "disk_encrypt"} <= skipped_names


class TestPostureCheckProbes:
    def test_sip_enabled_is_ok(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Darwin",
        ), mock.patch("shutil.which", side_effect=lambda b: "/fake/" + b), \
             mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(
                stdout=b"System Integrity Protection status: enabled.\n",
                stderr=b"",
                returncode=0,
            )
            r = _run(PostureCheckTool().execute({"checks": ["sip"]}, _ctx()))
        sip = next(c for c in r.output["checks"] if c["name"] == "sip")
        assert sip["state"] == "ok"
        assert sip["severity"] == "low"

    def test_sip_disabled_is_warn_high(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Darwin",
        ), mock.patch("shutil.which", side_effect=lambda b: "/fake/" + b), \
             mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(
                stdout=b"System Integrity Protection status: disabled.\n",
                stderr=b"",
                returncode=0,
            )
            r = _run(PostureCheckTool().execute({"checks": ["sip"]}, _ctx()))
        sip = next(c for c in r.output["checks"] if c["name"] == "sip")
        assert sip["state"] == "warn"
        assert sip["severity"] == "high"
        # Issue surfaced too.
        assert any(i["name"] == "sip" for i in r.output["issues"])
        assert r.output["overall_severity"] == "high"

    def test_filevault_off_is_warn(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Darwin",
        ), mock.patch("shutil.which", side_effect=lambda b: "/fake/" + b), \
             mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(
                stdout=b"FileVault is Off.\n",
                stderr=b"",
                returncode=0,
            )
            r = _run(PostureCheckTool().execute({"checks": ["filevault"]}, _ctx()))
        fv = next(c for c in r.output["checks"] if c["name"] == "filevault")
        assert fv["state"] == "warn"
        assert fv["severity"] == "high"

    def test_severity_override_lowers_finding(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Darwin",
        ), mock.patch("shutil.which", side_effect=lambda b: "/fake/" + b), \
             mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(
                stdout=b"System Integrity Protection status: disabled.\n",
                stderr=b"",
                returncode=0,
            )
            r = _run(PostureCheckTool().execute({
                "checks": ["sip"],
                "severity_overrides": {"sip": "medium"},
            }, _ctx()))
        sip = next(c for c in r.output["checks"] if c["name"] == "sip")
        # Override applied
        assert sip["severity"] == "medium"
        assert r.output["overall_severity"] == "medium"

    def test_pf_without_root_returns_unknown(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Darwin",
        ), mock.patch("shutil.which", side_effect=lambda b: "/fake/" + b), \
             mock.patch("subprocess.run") as run:
            # pfctl emits permission denied without status: line
            run.return_value = mock.Mock(
                stdout=b"",
                stderr=b"pfctl: Operation not permitted\n",
                returncode=1,
            )
            r = _run(PostureCheckTool().execute({"checks": ["firewall"]}, _ctx()))
        fw = next(c for c in r.output["checks"] if c["name"] == "firewall")
        assert fw["state"] == "unknown"
        # unknown should NOT bump severity
        assert fw["severity"] == "low"

    def test_alf_off_is_warn(self):
        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Darwin",
        ), mock.patch("shutil.which", side_effect=lambda b: "/fake/" + b), \
             mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(
                stdout=b"0\n",
                stderr=b"",
                returncode=0,
            )
            r = _run(PostureCheckTool().execute({"checks": ["app_firewall"]}, _ctx()))
        alf = next(c for c in r.output["checks"] if c["name"] == "app_firewall")
        assert alf["state"] == "warn"

    def test_overall_severity_is_max_across_checks(self):
        # Combine ok + warn-medium + warn-high → overall = high
        call_count = [0]

        def fake_run(*args, **kwargs):
            call_count[0] += 1
            n = call_count[0]
            return [
                # csrutil status (sip): enabled → ok
                mock.Mock(stdout=b"status: enabled.\n", stderr=b"", returncode=0),
                # fdesetup status (filevault): off → warn high
                mock.Mock(stdout=b"FileVault is Off.\n", stderr=b"", returncode=0),
                # spctl --status (gatekeeper): disabled → warn medium
                mock.Mock(stdout=b"assessments disabled\n", stderr=b"", returncode=0),
            ][n - 1]

        with mock.patch(
            "forest_soul_forge.tools.builtin.posture_check.platform.system",
            return_value="Darwin",
        ), mock.patch("shutil.which", side_effect=lambda b: "/fake/" + b), \
             mock.patch("subprocess.run", side_effect=fake_run):
            r = _run(PostureCheckTool().execute({
                "checks": ["sip", "filevault", "gatekeeper"],
            }, _ctx()))
        assert r.output["overall_severity"] == "high"
        # 2 issues (filevault + gatekeeper); sip is ok
        assert len(r.output["issues"]) == 2


# ============================================================================
# continuous_verify.v1
# ============================================================================
class TestContinuousVerifyValidation:
    def test_refuses_missing_current(self):
        with pytest.raises(ToolValidationError, match="current"):
            ContinuousVerifyTool().validate({})

    def test_refuses_non_dict_current(self):
        with pytest.raises(ToolValidationError, match="current"):
            ContinuousVerifyTool().validate({"current": "bad"})

    def test_refuses_current_without_checks_list(self):
        with pytest.raises(ToolValidationError, match="checks"):
            ContinuousVerifyTool().validate({"current": {}})

    def test_refuses_non_dict_baseline(self):
        with pytest.raises(ToolValidationError, match="baseline"):
            ContinuousVerifyTool().validate({
                "current": {"checks": []},
                "baseline": "bad",
            })

    def test_refuses_non_bool_escalate(self):
        with pytest.raises(ToolValidationError, match="escalate"):
            ContinuousVerifyTool().validate({
                "current": {"checks": []},
                "escalate_on_missing": "yes",
            })


class TestContinuousVerifyDrift:
    def _check(self, name, state="ok", severity="low"):
        return {"name": name, "state": state, "severity": severity, "value": "x"}

    def test_no_baseline_marks_all_added(self):
        current = {"checks": [
            self._check("sip"),
            self._check("firewall", "warn", "high"),
        ]}
        r = _run(ContinuousVerifyTool().execute({"current": current}, _ctx()))
        assert len(r.output["checks_added"]) == 2
        assert r.output["checks_changed"] == []
        # verdict = max severity across added items = high
        assert r.output["verdict"] == "high"

    def test_steady_state_yields_low_verdict(self):
        current = {"checks": [self._check("sip"), self._check("filevault")]}
        r = _run(ContinuousVerifyTool().execute({
            "current": current, "baseline": current,
        }, _ctx()))
        assert r.output["checks_added"] == []
        assert r.output["checks_changed"] == []
        assert r.output["checks_removed"] == []
        assert len(r.output["checks_steady"]) == 2
        assert r.output["verdict"] == "low"

    def test_state_flip_recorded_as_changed(self):
        old = {"checks": [self._check("sip", "ok", "low")]}
        new = {"checks": [self._check("sip", "warn", "high")]}
        r = _run(ContinuousVerifyTool().execute({
            "current": new, "baseline": old,
        }, _ctx()))
        assert len(r.output["checks_changed"]) == 1
        change = r.output["checks_changed"][0]
        assert change["from"]["state"] == "ok"
        assert change["to"]["state"] == "warn"
        # Worse-of-two severity: high
        assert change["severity"] == "high"
        assert r.output["severity_drift"] is True
        assert r.output["verdict"] == "high"

    def test_severity_escalation_to_critical(self):
        old = {"checks": [self._check("sip", "ok", "low")]}
        new = {"checks": [self._check("sip", "warn", "critical")]}
        r = _run(ContinuousVerifyTool().execute({
            "current": new, "baseline": old,
        }, _ctx()))
        assert r.output["verdict"] == "critical"

    def test_removed_check_default_medium(self):
        old = {"checks": [self._check("sip"), self._check("firewall")]}
        new = {"checks": [self._check("sip")]}
        r = _run(ContinuousVerifyTool().execute({
            "current": new, "baseline": old,
        }, _ctx()))
        removed = r.output["checks_removed"]
        assert len(removed) == 1
        assert removed[0]["name"] == "firewall"
        assert removed[0]["severity"] == "medium"

    def test_removed_check_escalates_when_flag_set(self):
        old = {"checks": [self._check("sip"), self._check("firewall")]}
        new = {"checks": [self._check("sip")]}
        r = _run(ContinuousVerifyTool().execute({
            "current": new, "baseline": old, "escalate_on_missing": True,
        }, _ctx()))
        assert r.output["checks_removed"][0]["severity"] == "high"
        assert r.output["verdict"] == "high"

    def test_severity_drift_only_set_on_severity_change(self):
        # Same state, same severity → not drift even if value changes
        old = {"checks": [
            {"name": "sip", "state": "ok", "severity": "low", "value": "v1"},
        ]}
        new = {"checks": [
            {"name": "sip", "state": "ok", "severity": "low", "value": "v2"},
        ]}
        r = _run(ContinuousVerifyTool().execute({
            "current": new, "baseline": old,
        }, _ctx()))
        # State + severity match; only value differs — should be steady.
        assert r.output["severity_drift"] is False

    def test_summary_describes_drift(self):
        current = {"checks": [self._check("sip"), self._check("firewall", "warn", "high")]}
        baseline = {"checks": [self._check("sip")]}
        r = _run(ContinuousVerifyTool().execute({
            "current": current, "baseline": baseline,
        }, _ctx()))
        assert "drift" in r.output["summary"]
        assert "1 new" in r.output["summary"]


# ============================================================================
# Registration sanity
# ============================================================================
class TestRegistration:
    def test_both_tools_register(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("posture_check", "1")
        assert reg.has("continuous_verify", "1")
