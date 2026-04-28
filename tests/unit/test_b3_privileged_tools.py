"""Unit tests for ADR-0033 Phase B3 — privileged tools (dynamic_policy + tamper_detect).

Covers:
- dynamic_policy.v1  (PrivClient.pf_add / pf_drop wrapper)
- tamper_detect.v1   (canary integrity + macOS SIP probe)
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.security.priv_client import HelperMissing, PrivClientError
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin import DynamicPolicyTool, TamperDetectTool


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
# dynamic_policy.v1
# ============================================================================
class _PrivResult:
    def __init__(self, ok=True, exit_code=0, stdout="ok", stderr=""):
        self.ok = ok
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class _MockPriv:
    def __init__(self, raise_=None, result=None):
        self.raise_ = raise_
        self.result = result or _PrivResult()
        self.calls = []

    def pf_add(self, anchor, rule):
        self.calls.append(("pf_add", anchor, rule))
        if self.raise_:
            raise self.raise_
        return self.result

    def pf_drop(self, anchor):
        self.calls.append(("pf_drop", anchor))
        if self.raise_:
            raise self.raise_
        return self.result


class TestDynamicPolicyValidation:
    @pytest.mark.parametrize("bad", [
        {},
        {"op": "unknown", "anchor": "a", "reason": "r"},
        {"op": "add", "anchor": "", "reason": "r"},
        {"op": "add", "anchor": "x" * 100, "reason": "r"},
        {"op": "add", "anchor": "a", "reason": "r"},                 # missing rule
        {"op": "add", "anchor": "a", "rule": "", "reason": "r"},
        {"op": "add", "anchor": "a", "rule": "r" * 300, "reason": "r"},
        {"op": "drop", "anchor": "a", "rule": "x", "reason": "r"},  # drop+rule
        {"op": "add", "anchor": "a", "rule": "r", "reason": ""},
        {"op": "add", "anchor": "a", "rule": "r", "reason": "x" * 300},
    ])
    def test_validation_refusals(self, bad):
        with pytest.raises(ToolValidationError):
            DynamicPolicyTool().validate(bad)


class TestDynamicPolicyExecution:
    def test_refuses_without_priv_client(self):
        ctx = _ctx(priv_client=None)
        with pytest.raises(ToolValidationError, match="PrivClient|helper"):
            _run(DynamicPolicyTool().execute({
                "op": "add", "anchor": "block",
                "rule": "block in proto tcp", "reason": "x",
            }, ctx))

    def test_add_calls_pf_add(self):
        priv = _MockPriv()
        ctx = _ctx(priv_client=priv)
        result = _run(DynamicPolicyTool().execute({
            "op": "add", "anchor": "block_lateral",
            "rule": "block in proto tcp from 1.2.3.4",
            "reason": "incident response",
        }, ctx))
        assert priv.calls == [
            ("pf_add", "block_lateral", "block in proto tcp from 1.2.3.4"),
        ]
        assert result.output["op"] == "add"
        assert result.output["anchor"] == "block_lateral"
        assert result.output["ok"] is True
        assert result.metadata["priv_op"] == "pf-add"
        assert result.metadata["priv_args"] == [
            "block_lateral", "block in proto tcp from 1.2.3.4",
        ]
        assert "block_lateral" in result.side_effect_summary

    def test_drop_calls_pf_drop_without_rule(self):
        priv = _MockPriv()
        ctx = _ctx(priv_client=priv)
        result = _run(DynamicPolicyTool().execute({
            "op": "drop", "anchor": "block_lateral",
            "reason": "incident closed",
        }, ctx))
        assert priv.calls == [("pf_drop", "block_lateral")]
        assert result.output["op"] == "drop"
        assert result.output["rule"] is None
        assert result.metadata["priv_op"] == "pf-drop"
        assert result.metadata["priv_args"] == ["block_lateral"]

    def test_failed_helper_call_reflected_in_output(self):
        priv = _MockPriv(result=_PrivResult(
            ok=False, exit_code=2, stdout="", stderr="rule rejected",
        ))
        ctx = _ctx(priv_client=priv)
        result = _run(DynamicPolicyTool().execute({
            "op": "add", "anchor": "x", "rule": "y", "reason": "r",
        }, ctx))
        assert result.output["ok"] is False
        assert result.output["exit_code"] == 2
        assert "rejected" in result.output["stderr"]
        assert "failed" in result.side_effect_summary

    def test_helper_missing_raises_validation_error(self):
        priv = _MockPriv(raise_=HelperMissing("not at /usr/local/sbin/fsf-priv"))
        ctx = _ctx(priv_client=priv)
        with pytest.raises(ToolValidationError, match="helper"):
            _run(DynamicPolicyTool().execute({
                "op": "add", "anchor": "x", "rule": "y", "reason": "r",
            }, ctx))

    def test_priv_client_error_raises_validation_error(self):
        priv = _MockPriv(raise_=PrivClientError("anchor invalid"))
        ctx = _ctx(priv_client=priv)
        with pytest.raises(ToolValidationError, match="refused"):
            _run(DynamicPolicyTool().execute({
                "op": "add", "anchor": "x", "rule": "y", "reason": "r",
            }, ctx))


# ============================================================================
# tamper_detect.v1
# ============================================================================
class TestTamperDetectValidation:
    @pytest.mark.parametrize("bad", [
        {},
        {"canaries": []},
        {"canaries": ["relative/path"]},
        {"canaries": ["/abs", ""]},
        {"canaries": ["/x"], "baseline_digests": "bad"},
        {"canaries": ["/x"], "sip_paths": "bad"},
        {"canaries": ["/x"], "sip_paths": ["relative"]},
        {"canaries": ["/x"], "probe_sip": "no"},
    ])
    def test_validation_refusals(self, bad):
        with pytest.raises(ToolValidationError):
            TamperDetectTool().validate(bad)

    def test_oversize_canary_list_refused(self):
        with pytest.raises(ToolValidationError, match="canaries"):
            TamperDetectTool().validate({
                "canaries": [f"/p/{i}" for i in range(60)],
            })


class TestTamperDetectCanaryScenarios:
    def test_no_baseline_marks_all_new(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        result = _run(TamperDetectTool().execute({
            "canaries": [str(a)],
        }, _ctx()))
        assert result.output["canary_results"][0]["status"] == "new"
        # 'new' is informational — verdict stays ok.
        assert result.output["verdict"] == "ok"

    def test_baseline_match_is_ok(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("canary")
        digest = "sha256:" + hashlib.sha256(b"canary").hexdigest()
        result = _run(TamperDetectTool().execute({
            "canaries": [str(a)],
            "baseline_digests": {str(a): digest},
        }, _ctx()))
        assert result.output["canary_results"][0]["status"] == "ok"
        assert result.output["verdict"] == "ok"

    def test_mismatch_yields_warn(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("v1")
        baseline = "sha256:" + hashlib.sha256(b"v1").hexdigest()
        a.write_text("TAMPERED")
        result = _run(TamperDetectTool().execute({
            "canaries": [str(a)],
            "baseline_digests": {str(a): baseline},
        }, _ctx()))
        assert result.output["canary_results"][0]["status"] == "mismatch"
        assert result.output["verdict"] == "warn"

    def test_vanished_yields_critical(self, tmp_path):
        gone = tmp_path / "never_existed.txt"
        baseline = "sha256:" + hashlib.sha256(b"x").hexdigest()
        result = _run(TamperDetectTool().execute({
            "canaries": [str(gone)],
            "baseline_digests": {str(gone): baseline},
        }, _ctx()))
        assert result.output["canary_results"][0]["status"] == "vanished"
        # Vanished is highest severity — implies cleanup attempt.
        assert result.output["verdict"] == "critical"

    def test_oversized_file_marked_error(self, tmp_path):
        # Mock the size cap so we don't actually write 10 MiB.
        a = tmp_path / "a.txt"
        a.write_text("x" * 100)
        with mock.patch(
            "forest_soul_forge.tools.builtin.tamper_detect._MAX_FILE_BYTES",
            10,
        ):
            result = _run(TamperDetectTool().execute({
                "canaries": [str(a)],
            }, _ctx()))
        assert result.output["canary_results"][0]["status"] == "error"
        assert "oversized" in result.output["canary_results"][0]["detail"]

    def test_mixed_mismatch_and_vanished_critical(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("v1")
        baseline_a = "sha256:" + hashlib.sha256(b"v1").hexdigest()
        a.write_text("TAMPERED")
        gone = tmp_path / "missing.txt"
        baseline_g = "sha256:" + hashlib.sha256(b"x").hexdigest()
        result = _run(TamperDetectTool().execute({
            "canaries": [str(a), str(gone)],
            "baseline_digests": {str(a): baseline_a, str(gone): baseline_g},
        }, _ctx()))
        statuses = {x["path"]: x["status"] for x in result.output["canary_results"]}
        assert statuses[str(a)] == "mismatch"
        assert statuses[str(gone)] == "vanished"
        assert result.output["verdict"] == "critical"
        assert result.output["findings_count"] == 2


class TestTamperDetectSipProbe:
    def test_skipped_on_linux(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        with mock.patch(
            "forest_soul_forge.tools.builtin.tamper_detect.platform.system",
            return_value="Linux",
        ):
            result = _run(TamperDetectTool().execute({
                "canaries": [str(a)],
                "probe_sip": True,
                "sip_paths": ["/etc/hostname"],
            }, _ctx()))
        skipped = [x for x in result.output["skipped"] if x["name"] == "sip"]
        assert skipped
        assert "darwin-only" in skipped[0]["reason"]
        assert result.output["sip_probes"] is None

    def test_skipped_when_no_priv_client(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        with mock.patch(
            "forest_soul_forge.tools.builtin.tamper_detect.platform.system",
            return_value="Darwin",
        ):
            result = _run(TamperDetectTool().execute({
                "canaries": [str(a)],
                "probe_sip": True,
                "sip_paths": ["/x"],
            }, _ctx(priv_client=None)))
        skipped = [x for x in result.output["skipped"] if x["name"] == "sip"]
        assert skipped
        assert "PrivClient" in skipped[0]["reason"]

    def test_skipped_when_no_sip_paths(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        priv = _MockPriv()
        with mock.patch(
            "forest_soul_forge.tools.builtin.tamper_detect.platform.system",
            return_value="Darwin",
        ):
            result = _run(TamperDetectTool().execute({
                "canaries": [str(a)],
                "probe_sip": True,
            }, _ctx(priv_client=priv)))
        skipped = [x for x in result.output["skipped"] if x["name"] == "sip"]
        assert skipped
        assert "no sip_paths" in skipped[0]["reason"]

    def test_happy_path_via_helper(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")

        class _SipPriv:
            def read_protected(self, path):
                # Mirror the helper's stdout shape.
                return _PrivResult(
                    ok=True, exit_code=0, stderr="",
                    stdout="sha256:" + "d" * 64 + " 4096 " + path,
                )

        with mock.patch(
            "forest_soul_forge.tools.builtin.tamper_detect.platform.system",
            return_value="Darwin",
        ):
            result = _run(TamperDetectTool().execute({
                "canaries": [str(a)],
                "probe_sip": True,
                "sip_paths": ["/System/Library/Kernels/kernel"],
            }, _ctx(priv_client=_SipPriv())))
        assert result.output["sip_probes"] == [
            {
                "path":   "/System/Library/Kernels/kernel",
                "ok":     True,
                "digest": "sha256:" + "d" * 64,
                "size":   4096,
                "detail": "helper readable",
            },
        ]
        assert result.output["verdict"] == "ok"

    def test_failed_probe_yields_critical(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")

        class _FailPriv:
            def read_protected(self, path):
                return _PrivResult(
                    ok=False, exit_code=2,
                    stdout="", stderr="helper denied",
                )

        with mock.patch(
            "forest_soul_forge.tools.builtin.tamper_detect.platform.system",
            return_value="Darwin",
        ):
            result = _run(TamperDetectTool().execute({
                "canaries": [str(a)],
                "probe_sip": True,
                "sip_paths": ["/System/Library/Kernels/kernel"],
            }, _ctx(priv_client=_FailPriv())))
        probe = result.output["sip_probes"][0]
        assert probe["ok"] is False
        assert "helper denied" in probe["detail"]
        assert result.output["verdict"] == "critical"

    def test_helper_missing_during_probe(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")

        class _MissingPriv:
            def read_protected(self, path):
                raise HelperMissing("not at /usr/local/sbin/fsf-priv")

        with mock.patch(
            "forest_soul_forge.tools.builtin.tamper_detect.platform.system",
            return_value="Darwin",
        ):
            result = _run(TamperDetectTool().execute({
                "canaries": [str(a)],
                "probe_sip": True,
                "sip_paths": ["/x"],
            }, _ctx(priv_client=_MissingPriv())))
        probe = result.output["sip_probes"][0]
        assert probe["ok"] is False
        assert "helper missing" in probe["detail"]
        # SIP probe failure → critical
        assert result.output["verdict"] == "critical"


# ============================================================================
# Registration sanity
# ============================================================================
class TestRegistration:
    def test_both_tools_register(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("dynamic_policy", "1")
        assert reg.has("tamper_detect", "1")

    def test_catalog_registry_consistency(self):
        from pathlib import Path
        from forest_soul_forge.core.tool_catalog import load_catalog
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        cat = load_catalog(Path(__file__).parent.parent.parent
                           / "config" / "tool_catalog.yaml")
        reg = ToolRegistry()
        register_builtins(reg)
        for key, tool in reg.tools.items():
            td = cat.tools.get(key)
            assert td is not None, f"{key} not in catalog"
            assert td.side_effects == tool.side_effects, (
                f"{key} side_effects: registry={tool.side_effects} "
                f"catalog={td.side_effects}"
            )
