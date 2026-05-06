"""``/admin/conversations`` — operator-only conversation maintenance.

ADR-003Y Y7: lazy summarization sweep. Walks turns whose body is
past their conversation's retention_policy window, dispatches
``llm_think.v1`` for each to produce a tight summary, then purges
the body via ``summarize_and_purge_body``.

Designed as operator-triggered (POST endpoint) rather than a
daemon-side scheduler. The daemon stays simple; the operator
chooses when to run sweeps. A future Y7.1 may add an asyncio
periodic task that calls this same code path on a schedule (every
hour by default).

The summarizer is the FIRST agent participant of each conversation
that has llm_think.v1 in its constitution. If the conversation has
no agent participants (operator-only journaling room), the turn is
skipped with status='no_summarizer_agent'. Operator can rotate the
summarizer agent by archiving + re-creating with a fresh
participant if the current one is misbehaving.

Provider posture inherits from the agent's standard runtime
(local-first by default; frontier opt-in via FSF_FRONTIER_ENABLED).
Retention sweep is summarization, NOT generation, so frontier opt-in
is genuinely the operator's call to make.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_tool_dispatcher,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    RetentionSweepEntry,
    RetentionSweepRequest,
    RetentionSweepResponse,
)
from forest_soul_forge.registry import Registry

router = APIRouter(prefix="/admin/conversations", tags=["conversations"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_days(now_iso: str, turn_iso: str) -> float:
    """Best-effort day-delta between two ISO timestamps. Returns 0.0
    on parse failure (better to skip than crash the sweep)."""
    try:
        now = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        turn = datetime.strptime(turn_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (now - turn).total_seconds() / 86400.0
    except Exception:
        return 0.0


def _build_summary_prompt(*, speaker_label: str, body: str) -> str:
    """Compact instruction for the summarizer. Tight + structured so
    summaries stay searchable without becoming distorted retellings."""
    return (
        "You are summarizing a single turn from a multi-turn conversation "
        "for retention-window archiving. The raw text below will be PURGED "
        "after you respond; only your summary will remain. Produce 1-2 "
        "sentences capturing the salient claims, decisions, or questions. "
        "Strip pleasantries; preserve names, numbers, and any open "
        "commitments. Do NOT invent.\n\n"
        f"Speaker: {speaker_label}\n"
        f"Turn body:\n---\n{body}\n---\n\n"
        "Summary:"
    )


@router.post(
    "/sweep_retention",
    response_model=RetentionSweepResponse,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def sweep_retention(
    body:            RetentionSweepRequest,
    registry:        Registry      = Depends(get_registry),
    audit:           AuditChain    = Depends(get_audit_chain),
    write_lock:      threading.Lock = Depends(get_write_lock),
    tool_dispatcher = Depends(get_tool_dispatcher),
) -> RetentionSweepResponse:
    """Y7: process turns past their retention window — summarize + purge.

    Each candidate turn is picked up, the conversation's first agent
    participant is selected as the summarizer, llm_think.v1 produces a
    summary, then summarize_and_purge_body atomically replaces body
    with the summary. ``conversation_summarized`` audit event is
    emitted per processed turn (or per failed one with the reason).
    """
    now = _utc_now_iso()

    with write_lock:
        candidates = registry.conversations.list_turns_due_for_summarization(
            now_iso=now, limit=body.limit,
        )

    entries: list[RetentionSweepEntry] = []
    summarized = 0
    skipped = 0
    failed = 0

    # Lazy-import to avoid circular at module-load time. Same pattern
    # the conversations router uses for the dispatch result types.
    from forest_soul_forge.tools.dispatcher import (
        DispatchSucceeded as _DispatchSucceeded,
    )

    for turn in candidates:
        age = _age_days(now, turn.timestamp)

        if body.dry_run:
            entries.append(RetentionSweepEntry(
                turn_id=turn.turn_id,
                conversation_id=turn.conversation_id,
                age_days=round(age, 2),
                status="dry_run",
                summary=None,
                error=None,
            ))
            continue

        # Pick a summarizer: first agent participant of the room.
        with write_lock:
            participants = registry.conversations.list_participants(
                turn.conversation_id,
            )

        summarizer = None
        for p in participants:
            try:
                a = registry.get_agent(p.instance_id)
                if a is not None:
                    summarizer = a
                    break
            except Exception:
                continue

        if summarizer is None:
            skipped += 1
            entries.append(RetentionSweepEntry(
                turn_id=turn.turn_id,
                conversation_id=turn.conversation_id,
                age_days=round(age, 2),
                status="no_summarizer_agent",
                error="no agent participant in this room — operator-only rooms can't auto-summarize",
            ))
            try:
                audit.append(
                    "conversation_summarized",
                    {
                        "conversation_id": turn.conversation_id,
                        "turn_id":         turn.turn_id,
                        "outcome":         "skipped_no_summarizer",
                        "age_days":        round(age, 2),
                    },
                    agent_dna=None,
                )
            except Exception:
                pass
            continue

        # Build prompt + dispatch llm_think against the summarizer.
        speaker_a = registry.get_agent(turn.speaker) if turn.speaker else None
        speaker_label = speaker_a.agent_name if speaker_a else turn.speaker
        prompt = _build_summary_prompt(speaker_label=speaker_label, body=turn.body or "")
        constitution_path = Path(summarizer.constitution_path)

        try:
            outcome = await tool_dispatcher.dispatch(
                instance_id=summarizer.instance_id,
                agent_dna=summarizer.dna,
                role=summarizer.role,
                genre=None,
                session_id=f"sweep-{turn.conversation_id}",
                constitution_path=constitution_path,
                tool_name="llm_think",
                tool_version="1",
                args={
                    "prompt":     prompt,
                    "task_kind":  "classify",
                    "max_tokens": body.summary_max_tokens,
                },
                provider=None,  # the dispatcher resolves via app.state.providers
            )
        except Exception as e:
            failed += 1
            entries.append(RetentionSweepEntry(
                turn_id=turn.turn_id,
                conversation_id=turn.conversation_id,
                age_days=round(age, 2),
                status="failed",
                error=f"dispatcher exception: {type(e).__name__}: {e}",
            ))
            continue

        if not isinstance(outcome, _DispatchSucceeded):
            failed += 1
            entries.append(RetentionSweepEntry(
                turn_id=turn.turn_id,
                conversation_id=turn.conversation_id,
                age_days=round(age, 2),
                status="failed",
                error=f"llm_think returned {type(outcome).__name__}",
            ))
            continue

        result_output = outcome.result.output or {}
        summary_text = (result_output.get("response") or "").strip()
        if not summary_text:
            failed += 1
            entries.append(RetentionSweepEntry(
                turn_id=turn.turn_id,
                conversation_id=turn.conversation_id,
                age_days=round(age, 2),
                status="failed",
                error="empty summary from llm_think",
            ))
            continue

        with write_lock:
            registry.conversations.summarize_and_purge_body(
                turn_id=turn.turn_id, summary=summary_text,
            )
            try:
                audit.append(
                    "conversation_summarized",
                    {
                        "conversation_id":    turn.conversation_id,
                        "turn_id":            turn.turn_id,
                        "outcome":            "summarized",
                        "age_days":           round(age, 2),
                        "summary_token_count": outcome.result.tokens_used,
                        "summarizer_dna":     summarizer.dna,
                        "dispatch_audit_seq": outcome.audit_seq,
                    },
                    agent_dna=summarizer.dna,
                )
            except Exception:
                pass

        summarized += 1
        entries.append(RetentionSweepEntry(
            turn_id=turn.turn_id,
            conversation_id=turn.conversation_id,
            age_days=round(age, 2),
            status="summarized",
            summary=summary_text,
        ))

    return RetentionSweepResponse(
        sweep_at=now,
        candidates=len(candidates),
        summarized=summarized,
        skipped=skipped,
        failed=failed,
        dry_run=body.dry_run,
        entries=entries,
    )
