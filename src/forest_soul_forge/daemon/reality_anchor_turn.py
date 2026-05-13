"""ADR-0063 T5 pre-turn Reality Anchor gate.

Thin shared helper consumed by the conversation runtime
(``daemon/routers/conversations.py``). For every assistant turn
about to be appended to a conversation, this module verifies the
turn's body against the operator-asserted ground truth catalog
BEFORE the turn lands.

## Policy (ADR-0063 D1 — same as T3 dispatcher gate)

- CRITICAL contradiction → REFUSE the turn. Caller skips the
  registry append + emits ``reality_anchor_turn_refused``. The
  HTTP response includes the refusal so the operator sees why
  the agent didn't speak.
- HIGH / MEDIUM / LOW contradiction → ALLOW the turn but emit
  ``reality_anchor_turn_flagged`` so the audit chain captures
  the warning. The operator sees the turn AND the flag in the
  audit trail.
- CONFIRMED / UNKNOWN / NOT_IN_SCOPE → ALLOW silently.

## Per-agent opt-out

Same constitutional opt-out as T3:

```yaml
reality_anchor:
  enabled: false
```

A creative-writing or fictional-roleplay agent can opt out so
fictional assertions don't trip every operator-asserted fact.

## Why a distinct event-type pair from T3

T3 emits ``reality_anchor_refused`` / ``reality_anchor_flagged``
on the DISPATCHER surface (gating tool calls). T5 emits
``reality_anchor_turn_refused`` / ``reality_anchor_turn_flagged``
on the CONVERSATION surface (gating assistant turns). Splitting
the events lets an auditor answer "what turns got blocked?"
separately from "what tool calls got blocked?" without parsing
the event_data shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forest_soul_forge.core.audit_chain import AuditChain


# ---- public surface -------------------------------------------------------


@dataclass(frozen=True)
class TurnAnchorResult:
    """What :func:`check_turn_against_anchor` returns to the caller.

    The conversation router branches on ``decision``:
      - "allow" — proceed with the registry append.
      - "refuse" — skip the append; surface the structured
        payload to the operator via the TurnDispatchResponse.

    Either way ``payload`` carries the verdict shape for the
    HTTP response. ``audit_emitted`` reports which event-type
    landed (allow may emit flagged; refuse always emits refused).
    """
    decision: str         # "allow" | "refuse"
    payload: dict[str, Any]
    audit_emitted: str | None


def check_turn_against_anchor(
    *,
    response_text: str,
    constitution_path: Path,
    audit: AuditChain,
    conversation_id: str,
    speaker_instance_id: str,
    speaker_agent_dna: str,
    corrections_table: Any = None,  # ADR-0063 T6 — RealityAnchorCorrectionsTable
) -> TurnAnchorResult:
    """Verify a planned assistant turn against operator ground truth.

    Returns a :class:`TurnAnchorResult`. Caller (conversation
    router) inspects ``decision`` to decide whether to append the
    turn. Audit events are emitted by THIS function — the caller
    doesn't re-emit.

    All failure paths inside this function degrade to
    ``decision="allow"`` because the Reality Anchor is NOT load-
    bearing. A broken anchor must never block the operator from
    receiving an agent turn.
    """
    # Lazy imports — keep daemon module-import cost low. These
    # are the same module-level helpers ADR-0063 T3 wires into
    # the governance pipeline.
    from forest_soul_forge.tools.dispatcher import (
        _reality_anchor_verify, _reality_anchor_opt_out,
    )

    # 1. Constitutional opt-out check.
    try:
        opted_out = _reality_anchor_opt_out(constitution_path)
    except Exception:
        opted_out = False
    if opted_out:
        return TurnAnchorResult(
            decision="allow",
            payload={"reason": "opted_out"},
            audit_emitted=None,
        )

    claim = (response_text or "").strip()
    if not claim:
        return TurnAnchorResult(
            decision="allow",
            payload={"reason": "empty_turn"},
            audit_emitted=None,
        )

    # 2. Run the verifier. Anything raises → allow.
    try:
        result = _reality_anchor_verify(claim, None)
    except Exception as e:
        return TurnAnchorResult(
            decision="allow",
            payload={"reason": "verifier_raised", "detail": repr(e)},
            audit_emitted=None,
        )

    verdict = result.get("verdict", "not_in_scope")
    if verdict != "contradicted":
        return TurnAnchorResult(
            decision="allow",
            payload={"verdict": verdict},
            audit_emitted=None,
        )

    # 3. We have contradicting fact(s). Find the worst-severity row.
    contradictions = [
        r for r in (result.get("by_fact") or [])
        if r.get("verdict") == "contradicted"
    ]
    if not contradictions:
        return TurnAnchorResult(
            decision="allow",
            payload={"reason": "verifier_returned_contradicted_with_empty_by_fact"},
            audit_emitted=None,
        )

    severity_rank = {
        "CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0,
    }
    contradictions.sort(
        key=lambda r: severity_rank.get(r.get("severity", "INFO"), -1),
        reverse=True,
    )
    worst = contradictions[0]
    worst_severity = worst.get("severity", "INFO")

    # Bound the body excerpt — full turn bodies can be long, but
    # the audit event should stay compact.
    excerpt = claim[:500]

    event_data = {
        "conversation_id":          conversation_id,
        "speaker":                  speaker_instance_id,
        "body_excerpt":             excerpt,
        "fact_id":                  worst.get("fact_id"),
        "fact_statement":           worst.get("statement"),
        "severity":                 worst_severity,
        "matched_terms":            list(worst.get("matched_terms") or []),
        "contradicting_fact_count": len(contradictions),
    }

    decision_label = "refused" if worst_severity == "CRITICAL" else "warned"

    if worst_severity == "CRITICAL":
        try:
            audit.append(
                "reality_anchor_turn_refused",
                event_data,
                agent_dna=speaker_agent_dna,
            )
        except Exception:
            pass
        _maybe_emit_turn_repeat_offender(
            audit=audit, corrections_table=corrections_table,
            claim=claim, worst=worst, decision=decision_label,
            conversation_id=conversation_id,
            speaker_instance_id=speaker_instance_id,
            speaker_agent_dna=speaker_agent_dna,
        )
        return TurnAnchorResult(
            decision="refuse",
            payload={
                "verdict":         "contradicted",
                "refused":         True,
                "severity":        worst_severity,
                "fact_id":         worst.get("fact_id"),
                "fact_statement":  worst.get("statement"),
                "matched_terms":   list(worst.get("matched_terms") or []),
                "by_fact":         contradictions,
            },
            audit_emitted="reality_anchor_turn_refused",
        )

    # HIGH / MEDIUM / LOW — flag but allow.
    try:
        audit.append(
            "reality_anchor_turn_flagged",
            event_data,
            agent_dna=speaker_agent_dna,
        )
    except Exception:
        pass
    _maybe_emit_turn_repeat_offender(
        audit=audit, corrections_table=corrections_table,
        claim=claim, worst=worst, decision=decision_label,
        conversation_id=conversation_id,
        speaker_instance_id=speaker_instance_id,
        speaker_agent_dna=speaker_agent_dna,
    )
    return TurnAnchorResult(
        decision="allow",
        payload={
            "verdict":   "contradicted",
            "refused":   False,
            "severity":  worst_severity,
            "fact_id":   worst.get("fact_id"),
        },
        audit_emitted="reality_anchor_turn_flagged",
    )


def _maybe_emit_turn_repeat_offender(
    *,
    audit: AuditChain,
    corrections_table: Any,
    claim: str,
    worst: dict,
    decision: str,
    conversation_id: str,
    speaker_instance_id: str,
    speaker_agent_dna: str,
) -> None:
    """ADR-0063 T6 conversation-surface hook. Mirrors the
    dispatcher-surface helper in RealityAnchorStep but emits
    surface='conversation' so an operator can separate the two
    surfaces in chain queries.

    No-op when ``corrections_table`` isn't wired (router not
    yet hooked up in test contexts). Any bumper failure
    degrades silently — the gate's primary refuse/flag
    decision is the load-bearing output.
    """
    if corrections_table is None:
        return
    try:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        count = corrections_table.bump_or_create(
            claim=claim,
            fact_id=worst.get("fact_id") or "unknown",
            worst_severity=worst.get("severity") or "INFO",
            now_iso=now_iso,
            agent_dna=speaker_agent_dna,
            instance_id=speaker_instance_id,
            decision=decision,
            surface="conversation",
        )
    except Exception:
        return
    if not isinstance(count, int) or count <= 1:
        return
    try:
        audit.append(
            "reality_anchor_repeat_offender",
            {
                "instance_id":      speaker_instance_id,
                "conversation_id":  conversation_id,
                "fact_id":          worst.get("fact_id"),
                "repetition_count": count,
                "decision":         decision,
                "surface":          "conversation",
                "claim":            claim[:500],
            },
            agent_dna=speaker_agent_dna,
        )
    except Exception:
        pass
