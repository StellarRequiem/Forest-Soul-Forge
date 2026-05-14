"""``decompose_intent.v1`` — ADR-0067 T2 (B280).

LLM-driven decomposition of an operator utterance into per-domain
sub-intents. Reads the domain registry (ADR-0067 T1) at call time
so the LLM's domain enumeration always reflects the live registry.

## Surface

Input:
  utterance (str, required) — the operator's natural-language utterance
  confidence_threshold (float, optional, default 0.6) — sub-intents
    with confidence below this surface as 'ambiguous' rather than
    routing automatically.
  task_kind (str, optional, default 'classify') — provider routing.

Output:
  {
    "utterance": "...",
    "subintents": [
      {
        "intent": "...",
        "domain": "d2_daily_life_os",
        "capability": "reminder",
        "confidence": 0.95,
        "status": "routable" | "ambiguous" | "no_match" | "planned_domain"
      }
    ],
    "ambiguity_count": int,  # subintents with status != "routable"
    "model": "...",
    "elapsed_ms": int
  }

## Why this is a tool and not a routing engine

T2 ships the decomposition primitive. The actual ROUTING (turning
sub-intents into delegate.v1 calls) is T3 (route_to_domain.v1) and
T4 (full engine). Keeping decomposition pure-LLM-call lets it stay
read_only side-effect tier — any agent (including the future
orchestrator) can ask "what does this utterance contain?" without
needing actuator privileges.

## Confidence floor

When the LLM emits a sub-intent with confidence below threshold,
or when the matched domain doesn't exist in the registry, the
sub-intent gets status='ambiguous' or 'no_match' rather than
silently routing to a bad guess. The downstream router (T3) refuses
to dispatch ambiguous sub-intents — surfaces them back to the
operator instead.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from forest_soul_forge.daemon.providers import TaskKind
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


DEFAULT_CONFIDENCE_THRESHOLD = 0.6
MAX_UTTERANCE_LEN = 4000
MIN_UTTERANCE_LEN = 2
MAX_DECOMPOSE_TOKENS = 600  # decomposition is JSON, doesn't need much


class DecomposeIntentTool:
    """Decompose an operator utterance into per-domain sub-intents.

    Pure read_only tool — no state mutation. Reads the domain
    registry at call time so new domains added to config/domains/
    show up without code changes.
    """

    name = "decompose_intent"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        utterance = args.get("utterance")
        if not isinstance(utterance, str):
            raise ToolValidationError(
                f"utterance is required and must be a string; "
                f"got {type(utterance).__name__}"
            )
        n = len(utterance)
        if n < MIN_UTTERANCE_LEN:
            raise ToolValidationError(
                f"utterance must be at least {MIN_UTTERANCE_LEN} chars"
            )
        if n > MAX_UTTERANCE_LEN:
            raise ToolValidationError(
                f"utterance too long ({n} chars > {MAX_UTTERANCE_LEN}); "
                f"the orchestrator handles one utterance at a time, "
                f"not document-scale decomposition"
            )
        threshold = args.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)
        if not isinstance(threshold, (int, float)) or threshold < 0 or threshold > 1:
            raise ToolValidationError(
                f"confidence_threshold must be in [0, 1]; got {threshold!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        provider = ctx.provider
        if provider is None:
            raise ToolValidationError(
                "decompose_intent.v1: no LLM provider wired. "
                "Decomposition requires a local model — check "
                "GET /runtime/provider and ensure Ollama is running."
            )

        # Load registry fresh — operator may have edited manifests
        # since boot.
        from forest_soul_forge.core.domain_registry import (
            DomainRegistryError,
            load_domain_registry,
        )
        try:
            registry, _registry_errors = load_domain_registry()
        except DomainRegistryError as e:
            raise ToolValidationError(
                f"decompose_intent.v1 cannot run without a loadable "
                f"domain registry: {e}"
            ) from e

        utterance: str = args["utterance"]
        threshold: float = float(args.get(
            "confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD,
        ))

        prompt = _build_decomposition_prompt(utterance, registry)
        system = _DECOMPOSITION_SYSTEM_PROMPT

        t0 = time.perf_counter()
        try:
            response = await provider.complete(
                prompt,
                task_kind=TaskKind.CLASSIFY,
                system=system,
                max_tokens=MAX_DECOMPOSE_TOKENS,
            )
        except Exception as e:
            raise ToolValidationError(
                f"decompose_intent.v1: provider call failed: "
                f"{type(e).__name__}: {e}"
            ) from e
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        raw_response = _extract_response_text(response)
        parsed = _parse_decomposition_response(raw_response)

        # Classify each sub-intent: routable / ambiguous / no_match /
        # planned_domain. The status field is what T3 (route_to_domain)
        # gates on — only 'routable' dispatches.
        valid_ids = set(registry.domain_ids())
        dispatchable_ids = set(registry.dispatchable_ids())
        for si in parsed:
            domain_id = si.get("domain", "")
            confidence = float(si.get("confidence", 0.0))
            if domain_id not in valid_ids:
                si["status"] = "no_match"
            elif domain_id not in dispatchable_ids:
                si["status"] = "planned_domain"
            elif confidence < threshold:
                si["status"] = "ambiguous"
            else:
                si["status"] = "routable"

        ambiguity_count = sum(1 for si in parsed if si["status"] != "routable")

        model_name = _extract_model_name(response)

        return ToolResult(
            success=True,
            output={
                "utterance": utterance,
                "subintents": parsed,
                "ambiguity_count": ambiguity_count,
                "model": model_name,
                "elapsed_ms": elapsed_ms,
            },
            audit_payload={
                "utterance_hash": _hash_utterance(utterance),
                "subintent_count": len(parsed),
                "ambiguity_count": ambiguity_count,
                "model": model_name,
            },
        )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


_DECOMPOSITION_SYSTEM_PROMPT = """You are the decomposition layer of \
a sovereign personal AI operating system. Given an operator utterance, \
break it into independent sub-intents and assign each one to the \
correct domain.

Output STRICT JSON only. No prose. No markdown. Just:
{
  "subintents": [
    {
      "intent": "<the sub-intent in operator's own words>",
      "domain": "<one of the listed domain_ids>",
      "capability": "<one of the listed capabilities for that domain>",
      "confidence": <0.0 to 1.0>
    }
  ]
}

Rules:
- One sub-intent per coherent task. "Remind me X and tell me Y" = 2 sub-intents.
- domain MUST be one of the listed domain_ids. Never invent.
- capability MUST be one of the listed capabilities for the chosen domain.
- confidence reflects YOUR uncertainty about the routing decision, not the
  difficulty of the task. 0.95 = "obvious"; 0.5 = "could be 2 domains"; 0.2 =
  "I'm guessing."
- If a sub-intent doesn't fit any domain, use domain="d_unknown",
  capability="unknown", confidence=0.0.
"""


def _build_decomposition_prompt(utterance: str, registry: Any) -> str:
    """Construct the per-call prompt: a list of every domain with
    its dispatchable capabilities + 1-2 example intents per domain,
    then the operator utterance."""
    lines: list[str] = []
    lines.append("DOMAIN CATALOG:")
    lines.append("")
    for domain in registry.domains:
        # Combine top-level capabilities + per-entry-agent capabilities,
        # de-duplicated, sorted for prompt stability.
        caps = set(domain.capabilities)
        for ea in domain.entry_agents:
            caps.add(ea.capability)
        cap_list = sorted(caps)
        status_marker = (
            "[live]" if domain.status == "live"
            else "[partial]" if domain.status == "partial"
            else "[planned — flag confidence below threshold]"
        )
        lines.append(
            f"  {domain.domain_id} — {domain.name} {status_marker}"
        )
        # Trim description to first line / 200 chars for prompt economy.
        desc_short = domain.description.split("\n")[0][:200]
        lines.append(f"    {desc_short}")
        lines.append(f"    capabilities: {', '.join(cap_list)}")
        if domain.example_intents:
            # 2 examples max per domain to keep prompt size manageable
            examples = list(domain.example_intents)[:2]
            for ex in examples:
                lines.append(f"    example: {ex}")
        lines.append("")
    lines.append("OPERATOR UTTERANCE:")
    lines.append(f'  "{utterance}"')
    lines.append("")
    lines.append("Decompose and output STRICT JSON only.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response parsing — robust against LLM JSON-with-prose
# ---------------------------------------------------------------------------


_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _parse_decomposition_response(raw: str) -> list[dict]:
    """Pull the subintents list out of the LLM's response.

    Robust to common LLM failure modes:
      - response wrapped in markdown code fence
      - prose before/after the JSON object
      - response is just an array instead of a wrapping object

    On any parse failure returns a single sub-intent with
    confidence=0.0 and a 'parse_failure' note so the downstream
    router surfaces the failure to the operator rather than silently
    dispatching to a bad guess.
    """
    if not isinstance(raw, str) or not raw.strip():
        return [_parse_failure_subintent("empty response")]

    # Try direct JSON parse first.
    candidate = raw.strip()
    # Strip common markdown fences.
    if candidate.startswith("```"):
        candidate = candidate.split("```", 2)[1] if "```" in candidate[3:] else candidate[3:]
        # Could be ```json\n...
        if candidate.startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
        if candidate.endswith("```"):
            candidate = candidate[:-3].strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        # Try to find the first JSON object anywhere in the text.
        match = _JSON_OBJECT_RE.search(raw)
        if match is None:
            return [_parse_failure_subintent(
                "no JSON object in response", raw_snippet=raw[:200],
            )]
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            return [_parse_failure_subintent(
                f"JSON object parse failed: {e}",
                raw_snippet=match.group(0)[:200],
            )]

    # Accept {subintents: [...]} or bare [...] form.
    if isinstance(parsed, list):
        subintents = parsed
    elif isinstance(parsed, dict):
        subintents = parsed.get("subintents")
        if not isinstance(subintents, list):
            return [_parse_failure_subintent(
                "response object missing 'subintents' list"
            )]
    else:
        return [_parse_failure_subintent(
            f"response root must be object or array; got {type(parsed).__name__}"
        )]

    # Normalize each sub-intent — pad missing fields with safe defaults.
    out: list[dict] = []
    for si in subintents:
        if not isinstance(si, dict):
            continue
        out.append({
            "intent": str(si.get("intent", "")),
            "domain": str(si.get("domain", "d_unknown")),
            "capability": str(si.get("capability", "unknown")),
            "confidence": float(si.get("confidence", 0.0)),
        })

    if not out:
        return [_parse_failure_subintent("subintents list was empty")]

    return out


def _parse_failure_subintent(
    reason: str, raw_snippet: str | None = None,
) -> dict:
    """Synthetic sub-intent that surfaces a parse failure to the
    operator without dispatching anything."""
    intent_text = f"<decomposition failed: {reason}>"
    if raw_snippet:
        intent_text += f" — raw: {raw_snippet}"
    return {
        "intent": intent_text,
        "domain": "d_unknown",
        "capability": "unknown",
        "confidence": 0.0,
    }


# ---------------------------------------------------------------------------
# Provider response shape adapters
# ---------------------------------------------------------------------------


def _extract_response_text(response: Any) -> str:
    """Pull the text out of the provider's return shape.

    Different providers return slightly different objects. We handle
    the common shapes: object with .text attribute, dict with
    'response' key, plain string.
    """
    if hasattr(response, "text"):
        return str(response.text)
    if isinstance(response, dict):
        for key in ("response", "text", "content", "completion"):
            if key in response:
                return str(response[key])
    if isinstance(response, str):
        return response
    return str(response)


def _extract_model_name(response: Any) -> str:
    """Pull the model name out of the provider's return shape, or
    'unknown' if not present."""
    if hasattr(response, "model"):
        return str(response.model)
    if isinstance(response, dict):
        for key in ("model", "model_name"):
            if key in response:
                return str(response[key])
    return "unknown"


# ---------------------------------------------------------------------------
# PII-safe utterance hashing for audit chain
# ---------------------------------------------------------------------------


def _hash_utterance(utterance: str) -> str:
    """Return a short hash of the utterance for audit_payload.

    The full utterance can contain personal data ('remind me to call
    Mom about the medical thing'). Audit chain entries should be
    forensic but not surveillance — we record a hash so an operator
    can confirm "was this MY utterance" without the chain itself
    storing the words.
    """
    import hashlib
    return hashlib.sha256(utterance.encode("utf-8")).hexdigest()[:16]
