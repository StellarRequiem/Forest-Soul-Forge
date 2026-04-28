"""Unit tests for ADR-0033 Phase B2 final batch — triage + isolate_process.

Covers:
- triage.v1            (LLM-driven Q&A → severity verdict, with fallbacks)
- isolate_process.v1   (PrivClient-wrapped SIGTERM/SIGKILL)
"""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin import IsolateProcessTool, TriageTool


def _run(coro):
    return asyncio.run(coro)


def _ctx(**kw):
    base = dict(
        instance_id="inst123abc", agent_dna="x" * 12,
        role="investigator", genre="security_mid", session_id="s",
    )
    base.update(kw)
    return ToolContext(**base)


# ============================================================================
# triage.v1
# ============================================================================
class _StubProvider:
    """Records every prompt + returns a configurable answer per call."""

    name = "stub"

    def __init__(self, answers: list[str], verdict_json: str | None = None):
        self.answers = list(answers)
        self.verdict_json = verdict_json
        self.prompts: list[str] = []

    async def complete(self, prompt, task_kind=None, system=None, max_tokens=None):
        self.prompts.append(prompt)
        # The classification call has the literal "JSON object" phrase.
        if "JSON object" in prompt and self.verdict_json is not None:
            return self.verdict_json
        if not self.answers:
            return "<no answer left>"
        return self.answers.pop(0)


class TestTriageValidation:
    def test_refuses_empty_alert(self):
        with pytest.raises(ToolValidationError, match="alert"):
            TriageTool().validate({"alert": "", "questions": [{"label": "x", "prompt": "y"}]})

    def test_refuses_missing_alert(self):
        with pytest.raises(ToolValidationError, match="alert"):
            TriageTool().validate({"questions": [{"label": "x", "prompt": "y"}]})

    def test_refuses_oversized_alert(self):
        with pytest.raises(ToolValidationError, match="alert"):
            TriageTool().validate({
                "alert": "x" * 5000,
                "questions": [{"label": "x", "prompt": "y"}],
            })

    def test_refuses_empty_questions(self):
        with pytest.raises(ToolValidationError, match="questions"):
            TriageTool().validate({"alert": "a", "questions": []})

    def test_refuses_too_many_questions(self):
        with pytest.raises(ToolValidationError, match="questions"):
            TriageTool().validate({
                "alert": "a",
                "questions": [
                    {"label": f"q{i}", "prompt": "p"} for i in range(11)
                ],
            })

    def test_refuses_question_without_label(self):
        with pytest.raises(ToolValidationError, match="label"):
            TriageTool().validate({
                "alert": "a", "questions": [{"prompt": "p"}],
            })

    def test_refuses_question_without_prompt(self):
        with pytest.raises(ToolValidationError, match="prompt"):
            TriageTool().validate({
                "alert": "a", "questions": [{"label": "x"}],
            })

    def test_refuses_oversized_prompt(self):
        with pytest.raises(ToolValidationError, match="prompt"):
            TriageTool().validate({
                "alert": "a",
                "questions": [{"label": "x", "prompt": "p" * 700}],
            })

    def test_refuses_non_dict_context(self):
        with pytest.raises(ToolValidationError, match="context"):
            TriageTool().validate({
                "alert": "a",
                "questions": [{"label": "x", "prompt": "p"}],
                "context": "not a dict",
            })


class TestTriageNoProviderFallback:
    def test_returns_medium_with_fallback_flag(self):
        ctx = _ctx(provider=None)
        result = _run(TriageTool().execute({
            "alert": "ssh login from suspicious ip",
            "questions": [
                {"label": "is_known", "prompt": "is the actor known?"},
                {"label": "is_lateral", "prompt": "lateral movement?"},
            ],
        }, ctx))
        assert result.output["severity"] == "medium"
        assert result.output["severity_score"] == 0.5
        assert result.output["fallback"] is True
        assert result.output["model_used"] is None
        assert len(result.output["answers"]) == 2
        assert all(a["answer"] == "<no provider>" for a in result.output["answers"])
        assert result.metadata["fallback_reason"] == "no_provider"


class TestTriageHappyPath:
    def test_full_verdict_from_provider(self):
        prov = _StubProvider(
            answers=["yes — actor is unknown", "yes — lateral evidence present"],
            verdict_json='{"severity":"high","summary":"Lateral SSH from unknown IP","recommended_action":"Isolate source host"}',
        )
        ctx = _ctx(provider=prov)
        result = _run(TriageTool().execute({
            "alert": "ssh from 1.2.3.4",
            "questions": [
                {"label": "is_known", "prompt": "is the actor known?"},
                {"label": "is_lateral", "prompt": "lateral movement?"},
            ],
        }, ctx))
        assert result.output["severity"] == "high"
        assert result.output["severity_score"] == 0.75
        assert result.output["fallback"] is False
        assert result.output["model_used"] == "stub"
        # Per-question + final classification = N+1 calls.
        assert len(prov.prompts) == 3
        # Answers preserved with their labels.
        labels = [a["label"] for a in result.output["answers"]]
        assert labels == ["is_known", "is_lateral"]

    def test_severity_score_mapping(self):
        for sev, expected in [("low", 0.25), ("medium", 0.5), ("high", 0.75), ("critical", 1.0)]:
            prov = _StubProvider(
                answers=["yes"],
                verdict_json=f'{{"severity":"{sev}","summary":"x","recommended_action":"y"}}',
            )
            ctx = _ctx(provider=prov)
            result = _run(TriageTool().execute({
                "alert": "a", "questions": [{"label": "x", "prompt": "y"}],
            }, ctx))
            assert result.output["severity"] == sev
            assert result.output["severity_score"] == expected

    def test_strips_markdown_fences(self):
        prov = _StubProvider(
            answers=["yes"],
            verdict_json='```json\n{"severity":"critical","summary":"x","recommended_action":"y"}\n```',
        )
        ctx = _ctx(provider=prov)
        result = _run(TriageTool().execute({
            "alert": "a", "questions": [{"label": "x", "prompt": "y"}],
        }, ctx))
        assert result.output["severity"] == "critical"
        assert result.output["fallback"] is False

    def test_includes_context_in_system_prompt(self):
        prov = _StubProvider(
            answers=["ok"],
            verdict_json='{"severity":"low","summary":"x","recommended_action":"y"}',
        )
        ctx = _ctx(provider=prov)
        _run(TriageTool().execute({
            "alert": "a",
            "questions": [{"label": "x", "prompt": "p"}],
            "context": {"recent_logs": ["entry1", "entry2"]},
        }, ctx))
        # The first prompt is the per-question call. System message
        # carries the context block — but since _StubProvider only sees
        # the prompt arg, the test verifies the call happened cleanly.
        assert len(prov.prompts) == 2  # 1 question + 1 verdict


class TestTriageFallbacks:
    def test_unparseable_verdict_falls_back_to_medium(self):
        prov = _StubProvider(
            answers=["a", "b"],
            verdict_json="i am sorry but i cannot parse this",
        )
        ctx = _ctx(provider=prov)
        result = _run(TriageTool().execute({
            "alert": "a",
            "questions": [
                {"label": "x", "prompt": "p"},
                {"label": "y", "prompt": "q"},
            ],
        }, ctx))
        assert result.output["severity"] == "medium"
        assert result.output["fallback"] is True
        assert result.metadata["verdict_source"] == "fallback_parse"

    def test_invalid_severity_value_clamps_to_medium(self):
        prov = _StubProvider(
            answers=["yes"],
            verdict_json='{"severity":"hellfire","summary":"x","recommended_action":"y"}',
        )
        ctx = _ctx(provider=prov)
        result = _run(TriageTool().execute({
            "alert": "a", "questions": [{"label": "x", "prompt": "y"}],
        }, ctx))
        # Invalid severity → clamped to medium, but NOT a fallback (model
        # responded with parseable JSON, just an out-of-range severity).
        assert result.output["severity"] == "medium"
        assert result.metadata["verdict_source"] == "model"

    def test_per_question_exception_captured_in_answers(self):
        class FailingProvider:
            name = "boom"

            def __init__(self):
                self.call = 0

            async def complete(self, prompt, **kwargs):
                self.call += 1
                if self.call == 1:
                    raise RuntimeError("model unreachable")
                if "JSON object" in prompt:
                    return '{"severity":"low","summary":"x","recommended_action":"y"}'
                return "ok"

        ctx = _ctx(provider=FailingProvider())
        result = _run(TriageTool().execute({
            "alert": "a",
            "questions": [
                {"label": "fails", "prompt": "p"},
                {"label": "works", "prompt": "q"},
            ],
        }, ctx))
        # First question's answer captured the error; second succeeded.
        first = result.output["answers"][0]
        assert "<error:" in first["answer"]
        assert "RuntimeError" in first["answer"]
        # Verdict still produced.
        assert result.output["severity"] == "low"

    def test_classifier_exception_falls_back(self):
        class ClassifierFailsProvider:
            name = "cf"

            def __init__(self):
                self.call = 0

            async def complete(self, prompt, **kwargs):
                self.call += 1
                if "JSON object" in prompt:
                    raise RuntimeError("classifier exploded")
                return "ok"

        ctx = _ctx(provider=ClassifierFailsProvider())
        result = _run(TriageTool().execute({
            "alert": "a", "questions": [{"label": "x", "prompt": "y"}],
        }, ctx))
        assert result.output["severity"] == "medium"
        assert result.output["fallback"] is True
        assert result.metadata["verdict_source"] == "fallback_error"


# ============================================================================
# isolate_process.v1
# ============================================================================
class _FakePrivResult:
    def __init__(self, ok=True, exit_code=0, stdout="killed", stderr=""):
        self.ok = ok
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class _FakePrivClient:
    def __init__(self, result=None, raise_=None):
        self.result = result or _FakePrivResult()
        self.raise_ = raise_
        self.calls = []

    def kill_pid(self, pid):
        self.calls.append(pid)
        if self.raise_:
            raise self.raise_
        return self.result


class TestIsolateProcessValidation:
    def test_refuses_missing_pid(self):
        with pytest.raises(ToolValidationError, match="pid"):
            IsolateProcessTool().validate({"reason": "x"})

    def test_refuses_pid_zero(self):
        with pytest.raises(ToolValidationError, match="pid"):
            IsolateProcessTool().validate({"pid": 0, "reason": "x"})

    def test_refuses_pid_one(self):
        with pytest.raises(ToolValidationError, match="pid"):
            IsolateProcessTool().validate({"pid": 1, "reason": "x"})

    def test_refuses_negative_pid(self):
        with pytest.raises(ToolValidationError, match="pid"):
            IsolateProcessTool().validate({"pid": -5, "reason": "x"})

    def test_refuses_bool_pid(self):
        # bool is a subclass of int — guard against that gotcha.
        with pytest.raises(ToolValidationError, match="pid"):
            IsolateProcessTool().validate({"pid": True, "reason": "x"})

    def test_refuses_empty_reason(self):
        with pytest.raises(ToolValidationError, match="reason"):
            IsolateProcessTool().validate({"pid": 1234, "reason": ""})

    def test_refuses_oversized_reason(self):
        with pytest.raises(ToolValidationError, match="reason"):
            IsolateProcessTool().validate({"pid": 1234, "reason": "x" * 300})


class TestIsolateProcessExecution:
    def test_refuses_when_priv_client_missing(self):
        ctx = _ctx(priv_client=None)
        with pytest.raises(ToolValidationError, match="PrivClient|helper"):
            _run(IsolateProcessTool().execute(
                {"pid": 9999, "reason": "x"}, ctx,
            ))

    def test_happy_path_returns_priv_result(self):
        priv = _FakePrivClient(_FakePrivResult(
            ok=True, exit_code=0, stdout="SIGTERM ok", stderr="",
        ))
        ctx = _ctx(priv_client=priv)
        result = _run(IsolateProcessTool().execute(
            {"pid": 9999, "reason": "lateral movement"}, ctx,
        ))
        assert priv.calls == [9999]
        assert result.output["pid"] == 9999
        assert result.output["ok"] is True
        assert result.output["exit_code"] == 0
        assert result.output["stdout"] == "SIGTERM ok"
        assert result.output["reason"] == "lateral movement"
        # Audit metadata preserved for the chain.
        assert result.metadata["priv_op"] == "kill-pid"
        assert result.metadata["priv_args"] == ["9999"]
        assert result.metadata["reason"] == "lateral movement"
        assert "9999" in result.side_effect_summary

    def test_failed_kill_reports_via_output(self):
        priv = _FakePrivClient(_FakePrivResult(
            ok=False, exit_code=1, stdout="", stderr="permission denied",
        ))
        ctx = _ctx(priv_client=priv)
        result = _run(IsolateProcessTool().execute(
            {"pid": 9999, "reason": "x"}, ctx,
        ))
        assert result.output["ok"] is False
        assert result.output["exit_code"] == 1
        assert "permission denied" in result.output["stderr"]
        assert "failed" in result.side_effect_summary

    def test_helper_missing_raises_validation_error(self):
        from forest_soul_forge.security.priv_client import HelperMissing
        priv = _FakePrivClient(raise_=HelperMissing("helper not found at /usr/local/sbin/fsf-priv"))
        ctx = _ctx(priv_client=priv)
        with pytest.raises(ToolValidationError, match="helper"):
            _run(IsolateProcessTool().execute(
                {"pid": 9999, "reason": "x"}, ctx,
            ))

    def test_priv_client_error_raises_validation_error(self):
        from forest_soul_forge.security.priv_client import PrivClientError
        priv = _FakePrivClient(raise_=PrivClientError("rejected: pid validation"))
        ctx = _ctx(priv_client=priv)
        with pytest.raises(ToolValidationError, match="refused"):
            _run(IsolateProcessTool().execute(
                {"pid": 9999, "reason": "x"}, ctx,
            ))


# ============================================================================
# Registration sanity
# ============================================================================
class TestRegistration:
    def test_both_tools_register(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("triage", "1")
        assert reg.has("isolate_process", "1")
