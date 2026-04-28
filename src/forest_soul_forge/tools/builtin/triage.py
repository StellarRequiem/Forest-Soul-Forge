"""``triage.v1`` — LLM-driven alert triage with diagnostic questions.

ADR-0033 Phase B2. ResponseRogue's first move when an alert
fires: feed it the alert + relevant context, ask a structured
list of diagnostic questions, score the answers into a single
severity classification (low / medium / high / critical), and
emit the reasoning so the operator (and future agents recalling
via memory) can understand why.

Pattern: each question has a label + prompt. The tool calls the
provider once per question, captures the answer string, then
runs a final classification call that takes all (question,
answer) pairs and returns a JSON-shaped {severity, summary,
recommended_action}.

side_effects=network — every call goes through the agent's
provider (Ollama / frontier). Per the genre approval policy:
security_high gates network calls; security_mid passes them
(investigators need to make routing decisions live).

Caps: 10 questions max per call, 600-char prompt per question,
60-second total timeout across all provider calls.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_QUESTIONS = 10
_MAX_PROMPT_CHARS = 600
_MAX_ALERT_CHARS = 4000
_TOTAL_TIMEOUT_SECONDS = 60
_VALID_SEVERITIES = ("low", "medium", "high", "critical")


class TriageTool:
    """Drive a structured diagnostic Q&A and emit a severity verdict.

    Args:
      alert     (str, required): the triggering alert text. ≤ 4000 chars.
      questions (list[object], required): each has {label, prompt}.
        Labels are short identifiers (e.g. "is_lateral", "is_known_actor")
        echoed in the output. Prompts are the full questions sent to
        the model. ≤ 10 questions per call.
      context (object, optional): extra fields included in every
        question's prompt for grounding (e.g. relevant memory recall,
        process snapshot). Truncated if oversized.

    Output:
      {
        "severity":            "low"|"medium"|"high"|"critical",
        "severity_score":      float,    # 0..1 mapped from the verdict
        "summary":             str,
        "recommended_action":  str,
        "answers": [
          {"label": str, "answer": str}, ...
        ],
        "model_used":          str | null,
        "fallback":            bool,    # true when no provider was bound
      }

    When ``ctx.provider`` is None (test contexts, daemons without an
    LLM configured), the tool emits a deterministic fallback verdict
    of severity='medium' so skills that depend on it still complete —
    operators see the fallback flag and know the LLM didn't run.
    """

    name = "triage"
    version = "1"
    side_effects = "network"

    def validate(self, args: dict[str, Any]) -> None:
        alert = args.get("alert")
        if not isinstance(alert, str) or not alert.strip():
            raise ToolValidationError("alert must be a non-empty string")
        if len(alert) > _MAX_ALERT_CHARS:
            raise ToolValidationError(
                f"alert must be ≤ {_MAX_ALERT_CHARS} chars; got {len(alert)}"
            )
        questions = args.get("questions")
        if not isinstance(questions, list) or not questions:
            raise ToolValidationError(
                "questions must be a non-empty list of {label, prompt}"
            )
        if len(questions) > _MAX_QUESTIONS:
            raise ToolValidationError(
                f"questions must be ≤ {_MAX_QUESTIONS}; got {len(questions)}"
            )
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                raise ToolValidationError(
                    f"questions[{i}] must be a dict"
                )
            if not isinstance(q.get("label"), str) or not q["label"]:
                raise ToolValidationError(
                    f"questions[{i}].label must be a non-empty string"
                )
            if not isinstance(q.get("prompt"), str) or not q["prompt"]:
                raise ToolValidationError(
                    f"questions[{i}].prompt must be a non-empty string"
                )
            if len(q["prompt"]) > _MAX_PROMPT_CHARS:
                raise ToolValidationError(
                    f"questions[{i}].prompt must be ≤ {_MAX_PROMPT_CHARS} chars"
                )
        if "context" in args and not isinstance(args["context"], dict):
            raise ToolValidationError(
                "context must be an object when provided"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        alert = args["alert"]
        questions = args["questions"]
        context_obj = args.get("context") or {}
        provider = getattr(ctx, "provider", None)

        # No provider → deterministic fallback so the tool is still
        # usable in tests + daemons without an LLM. Severity defaults
        # to 'medium' so the caller doesn't accidentally treat a
        # missing-LLM run as "all clear."
        if provider is None:
            return ToolResult(
                output={
                    "severity":           "medium",
                    "severity_score":     0.5,
                    "summary":            "No model provider available; deterministic fallback verdict.",
                    "recommended_action": "Re-run after enabling a provider for higher-fidelity triage.",
                    "answers":            [
                        {"label": q["label"], "answer": "<no provider>"}
                        for q in questions
                    ],
                    "model_used":         None,
                    "fallback":           True,
                },
                metadata={
                    "fallback_reason": "no_provider",
                    "questions_count": len(questions),
                },
                tokens_used=None, cost_usd=None,
                side_effect_summary=f"triage fallback: {len(questions)} questions, no LLM",
            )

        # Build the per-question system message. The same alert + context
        # ground every question so answers stay coherent.
        ctx_block = ""
        if context_obj:
            try:
                ctx_block = "\n\nContext:\n" + json.dumps(
                    context_obj, indent=2, default=str,
                )[:2000]
            except (TypeError, ValueError):
                ctx_block = ""
        system = (
            "You are a security incident triage assistant. Answer each "
            "question concisely (≤ 80 words) using only the alert and "
            "context supplied. If the information is insufficient to "
            "answer, say so explicitly — do not speculate.\n\n"
            f"Alert:\n{alert}{ctx_block}"
        )

        answers: list[dict[str, str]] = []
        try:
            await asyncio.wait_for(
                self._run_questions(provider, system, questions, answers),
                timeout=_TOTAL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # Partial answers preserved — caller sees what we got.
            return ToolResult(
                output={
                    "severity":           "medium",
                    "severity_score":     0.5,
                    "summary":            f"Triage timed out after {_TOTAL_TIMEOUT_SECONDS}s; {len(answers)}/{len(questions)} questions answered.",
                    "recommended_action": "Reduce question count or simplify prompts; re-run.",
                    "answers":            answers,
                    "model_used":         getattr(provider, "name", "unknown"),
                    "fallback":           True,
                },
                metadata={"fallback_reason": "timeout"},
                tokens_used=None, cost_usd=None,
                side_effect_summary=f"triage timed out at {len(answers)}/{len(questions)}",
            )

        # Final classification call: pass the (question, answer) pairs
        # back to the model and ask for a structured verdict.
        verdict = await _classify(provider, alert, answers)

        return ToolResult(
            output={
                "severity":           verdict["severity"],
                "severity_score":     _severity_to_score(verdict["severity"]),
                "summary":            verdict["summary"],
                "recommended_action": verdict["recommended_action"],
                "answers":            answers,
                "model_used":         getattr(provider, "name", "unknown"),
                "fallback":           verdict.get("fallback", False),
            },
            metadata={
                "questions_count": len(questions),
                "verdict_source":  verdict.get("source", "model"),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"triage: {verdict['severity']} — "
                f"{verdict['summary'][:60]}"
            ),
        )

    async def _run_questions(
        self, provider, system: str, questions: list[dict],
        out: list[dict[str, str]],
    ) -> None:
        from forest_soul_forge.daemon.providers.base import TaskKind
        for q in questions:
            try:
                answer = await provider.complete(
                    q["prompt"],
                    task_kind=TaskKind.CLASSIFY,
                    system=system,
                    max_tokens=200,
                )
            except Exception as e:
                answer = f"<error: {type(e).__name__}: {str(e)[:80]}>"
            out.append({"label": q["label"], "answer": str(answer).strip()})


async def _classify(provider, alert: str, answers: list[dict]) -> dict:
    """Final pass: model summarizes (q, a) → severity verdict.
    Returns a normalized dict; falls back to a deterministic
    'medium' verdict when the model output can't be parsed."""
    from forest_soul_forge.daemon.providers.base import TaskKind
    qa_block = "\n\n".join(
        f"Q ({a['label']}): {a['answer']}" for a in answers
    )
    classify_prompt = (
        "Based on the diagnostic answers below, emit a single JSON "
        "object with keys: severity (one of low/medium/high/critical), "
        "summary (≤ 100 words), recommended_action (≤ 60 words). No "
        "other text.\n\nAlert:\n" + alert + "\n\nDiagnostics:\n" + qa_block
    )
    try:
        raw = await provider.complete(
            classify_prompt,
            task_kind=TaskKind.GENERATE,
            system="You output ONLY valid JSON. No prose, no code fences.",
            max_tokens=400,
        )
    except Exception as e:
        return {
            "severity":           "medium",
            "summary":            f"Classification failed: {type(e).__name__}: {str(e)[:80]}",
            "recommended_action": "Investigate manually; the LLM classifier was unreachable.",
            "fallback":           True,
            "source":             "fallback_error",
        }
    # Strip code fences if the model added them despite the system msg.
    raw_clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw).strip(),
                       flags=re.MULTILINE)
    try:
        parsed = json.loads(raw_clean)
    except json.JSONDecodeError:
        return {
            "severity":           "medium",
            "summary":            "Model returned non-JSON; defaulting to medium severity.",
            "recommended_action": "Rerun with a smaller question set or different model.",
            "fallback":           True,
            "source":             "fallback_parse",
        }
    sev = str(parsed.get("severity", "medium")).lower().strip()
    if sev not in _VALID_SEVERITIES:
        sev = "medium"
    return {
        "severity":           sev,
        "summary":            str(parsed.get("summary", ""))[:600],
        "recommended_action": str(parsed.get("recommended_action", ""))[:400],
        "source":             "model",
    }


def _severity_to_score(sev: str) -> float:
    return {
        "low":      0.25,
        "medium":   0.5,
        "high":     0.75,
        "critical": 1.0,
    }.get(sev, 0.5)
