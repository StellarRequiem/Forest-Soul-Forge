"""Pure-function helpers for the conversation runtime.

Extracted from ``conversations.py`` during the 2026-04-30 Phase C
decomposition. These functions are stateless — given inputs, return
outputs — and have no FastAPI / dependency-injection coupling. That
makes them unit-testable in isolation (which the integration tests
were the only previous coverage path).

Three groups:

  - **Row → Pydantic adapters** (``_conversation_out``,
    ``_participant_out``, ``_turn_out``). One-line dataclass→Pydantic
    converters used by every endpoint that returns one of these
    shapes.

  - **Prompt builders** (``build_conversation_prompt``,
    ``build_ambient_prompt``). Compose the LLM prompt for a
    Y2/Y3 reactive turn or a Y5 ambient nudge from the agent's
    identity + recent room history.

  - **Ambient-mode gate readers** (``read_ambient_opt_in``,
    ``ambient_quota_used``). Inspect the constitution YAML for the
    opt-in flag, and walk the audit chain tail to count recent
    ambient_nudge events for quota enforcement.

Naming: where the original was prefixed with ``_`` to mark it
private to the module, the extracted version drops the underscore
because it's now intentionally module-public for the routers that
need it. The DI helpers (``_resolve_active_provider``) move to
``conversation_shared.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.daemon.schemas import (
    ConversationOut,
    ParticipantOut,
    TurnOut,
)


# ---------------------------------------------------------------------------
# Row → Pydantic adapters.
# ---------------------------------------------------------------------------
def conversation_out(row: Any) -> ConversationOut:
    """Adapt a ConversationRow registry dataclass to its Pydantic shape."""
    return ConversationOut(
        conversation_id=row.conversation_id,
        domain=row.domain,
        operator_id=row.operator_id,
        created_at=row.created_at,
        last_turn_at=row.last_turn_at,
        status=row.status,
        retention_policy=row.retention_policy,
    )


def participant_out(row: Any) -> ParticipantOut:
    """Adapt a ParticipantRow registry dataclass to its Pydantic shape."""
    return ParticipantOut(
        conversation_id=row.conversation_id,
        instance_id=row.instance_id,
        joined_at=row.joined_at,
        bridged_from=row.bridged_from,
    )


def turn_out(row: Any) -> TurnOut:
    """Adapt a TurnRow registry dataclass to its Pydantic shape."""
    return TurnOut(
        turn_id=row.turn_id,
        conversation_id=row.conversation_id,
        speaker=row.speaker,
        addressed_to=row.addressed_to,
        body=row.body,
        summary=row.summary,
        body_hash=row.body_hash,
        token_count=row.token_count,
        timestamp=row.timestamp,
        model_used=row.model_used,
    )


# ---------------------------------------------------------------------------
# Prompt builders.
# ---------------------------------------------------------------------------
def build_conversation_prompt(
    *,
    agent_name: str,
    agent_role: str,
    domain:     str,
    turns:      list,
    memories:   list[dict] | None = None,
) -> str:
    """Build the llm_think prompt for a Y2/Y3 conversation reply.

    Layout: a brief frame ("you are <name> in domain <X>"), an
    optional persistent-context block (ADR-0047 T5 — used by the
    Persistent Assistant chat to expose memory_recall.v1 results
    in the prompt), the conversation history rendered as
    'speaker: body' lines (oldest first), and a closing instruction.
    Purged turns surface as 'speaker: [summarized] <summary>' so the
    agent has continuity even past the retention window.

    ``memories`` is an optional list of memory entries (each a dict
    with at minimum a ``content`` field). Empty / None means no
    persistent-context block emits — preserves byte-for-byte prompt
    shape for multi-agent rooms where memory_recall stays opt-in
    per-agent rather than auto-injected.
    """
    lines: list[str] = []
    lines.append(
        f"You are {agent_name}, an agent in role '{agent_role}' "
        f"participating in a conversation in domain '{domain}'."
    )
    if memories:
        lines.append("")
        lines.append(
            "Persistent context (from your memory — facts you've "
            "accumulated across earlier sessions):"
        )
        for m in memories:
            content = (
                m.get("content")
                or m.get("body")
                or m.get("summary")
                or ""
            )
            if not content:
                continue
            # One bullet per memory; keep each on a single visual line
            # for the model. Long memories get truncated only if they
            # would blow up the prompt budget — recall already returns
            # at limit so this is usually a no-op.
            lines.append(f"  - {content}")
    lines.append("")
    lines.append("Recent conversation (oldest → newest):")
    for t in turns:
        body_or_summary = t.body if t.body is not None else (
            f"[summarized] {t.summary or '(content purged, no summary available)'}"
        )
        lines.append(f"  {t.speaker}: {body_or_summary}")
    lines.append("")
    lines.append(
        "Respond as " + agent_name + ", in character with your role. "
        "Keep the response focused and grounded; do not pretend to be "
        "another participant. Speak only as yourself."
    )
    return "\n".join(lines)


def build_ambient_prompt(
    *,
    agent_name: str,
    agent_role: str,
    domain:     str,
    nudge_kind: str,
    turns:      list,
) -> str:
    """Build a Y5 ambient-nudge prompt that asks the agent to surface
    SOMETHING NEW rather than re-answer the latest operator turn.

    Distinguishes ambient mode from reactive Y2/Y3 dispatches by
    framing the request as proactive contribution, not a response.
    """
    lines: list[str] = []
    lines.append(
        f"You are {agent_name}, an agent in role '{agent_role}' "
        f"participating in a conversation in domain '{domain}'. "
        f"This is an AMBIENT nudge of kind '{nudge_kind}' — the operator "
        "has invited you to proactively surface something useful, NOT "
        "to answer a recent message."
    )
    lines.append("")
    lines.append("Recent conversation (oldest → newest):")
    for t in turns:
        body_or_summary = t.body if t.body is not None else (
            f"[summarized] {t.summary or '(content purged)'}"
        )
        lines.append(f"  {t.speaker}: {body_or_summary}")
    lines.append("")
    lines.append(
        f"Surface ONE concise new contribution as {agent_name}: a follow-up "
        "question, a check-in, an observation, an open thread the room hasn't "
        "addressed yet. Keep it 1-3 sentences. Don't summarize what's already "
        "been said. Don't pretend to be another participant."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ambient-mode gate readers.
# ---------------------------------------------------------------------------
def read_ambient_opt_in(constitution_path: Path) -> bool:
    """Read ``interaction_modes.ambient_opt_in`` from the agent's
    constitution.yaml. Default False — Y5 is structurally opt-in
    even when the genre would permit it.

    Errors (missing file, malformed YAML, unexpected shape) all
    return False — the safer default for an opt-in gate.
    """
    import yaml
    if not constitution_path.exists():
        return False
    try:
        data = yaml.safe_load(constitution_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    block = data.get("interaction_modes") or {}
    if isinstance(block, dict):
        return bool(block.get("ambient_opt_in", False))
    return False


def ambient_quota_used(
    *, audit_chain, instance_id: str, conversation_id: str,
) -> int:
    """Count ``ambient_nudge`` events in the last 24h for the
    (agent, conversation) tuple. Walks the in-memory tail; cheap at
    realistic scales because ambient quotas are 1-10 per day.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    used = 0
    # tail(N) gives newest-first; we want last 24h so a generous
    # tail covers the worst case (a maxed-out heavy rate is 10/day
    # so ~10 entries at most per tuple).
    for entry in audit_chain.tail(500) or []:
        if entry.event_type != "ambient_nudge":
            continue
        if entry.timestamp < cutoff_iso:
            break  # rest are older than 24h
        d = entry.event_data or {}
        if d.get("instance_id") == instance_id and d.get("conversation_id") == conversation_id:
            used += 1
    return used


def resolve_active_provider(request) -> Any:
    """Best-effort active-provider resolution from app.state.

    Mirror of ``skills_run._resolve_active_provider`` so conversation
    dispatches use the same provider plumbing. Tools that don't need
    a provider tolerate None.
    """
    pr = getattr(request.app.state, "providers", None)
    if pr is None:
        return None
    try:
        return pr.active()
    except Exception:
        return None
