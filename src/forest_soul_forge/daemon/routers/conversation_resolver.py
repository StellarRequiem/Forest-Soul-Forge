"""Y3 multi-agent addressee resolution and @mention parsing.

Pure functions extracted from the conversations router so the
orchestration logic stays testable + the router file stays focused
on HTTP wiring. No daemon state lives here; callers thread the
agent registry / participants list in.

Resolution order (per ADR-003Y Y3):

  1. ``addressed_to`` is explicitly set → only those agents respond,
     in the order given. Caller-explicit; we trust them.
  2. ``addressed_to`` is empty AND the body contains ``@AgentName``
     mentions matching room participants → those agents respond, in
     mention order, deduped.
  3. Neither addressing nor mentions → fallback to the first agent
     participant in the room. Y3 keeps this simple; Y3.5 / Y4
     considers integrating ``suggest_agent.v1`` for richer fallback
     resolution.

After an agent responds, its body is parsed for new ``@AgentName``
mentions. Those become the next addressees in the chain. The chain
stops when:
  - No new mentions parsed (natural end), OR
  - ``max_chain_depth`` reached (default 4 per ADR-003Y), OR
  - An agent dispatch returns non-success (refused / failed /
    pending / provider error).

Self-pass is filtered: an agent that mentions itself is NOT
re-dispatched (otherwise a single agent could DoS its own quota).
"""
from __future__ import annotations

import re
from typing import Any, Callable

# @AgentName matches a-zA-Z0-9 + underscore + hyphen. Conservative —
# Forest agent_names use underscores ("Atlas_1777519605") and the
# suffix is digits, so this catches them. Excludes punctuation that
# would naturally end a sentence (".", "?", "!", ",").
_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_\-]+)")


def parse_mentions(body: str, name_to_id: dict[str, str]) -> list[str]:
    """Extract @AgentName references from ``body`` that match a known
    participant. Returns instance_ids in first-mention order, deduped.

    Matching is exact-name preferred, falling back to case-insensitive
    if no exact hit. Empty body or empty name_to_id returns [].
    """
    if not body or not name_to_id:
        return []
    seen: set[str] = set()
    out: list[str] = []
    # Lowercase index for case-insensitive fallback.
    lower_index: dict[str, str] = {n.lower(): iid for n, iid in name_to_id.items()}

    for m in _MENTION_PATTERN.finditer(body):
        candidate = m.group(1)
        iid = name_to_id.get(candidate)
        if iid is None:
            iid = lower_index.get(candidate.lower())
        if iid is None:
            continue
        if iid in seen:
            continue
        seen.add(iid)
        out.append(iid)
    return out


def resolve_initial_addressees(
    *,
    addressed_to:    list[str] | None,
    body:            str,
    participants:    list[Any],  # ParticipantRow list
    agent_lookup_fn: Callable[[str], Any],  # instance_id -> agent_row
) -> list[str]:
    """Resolve who responds to an operator turn (or whoever spoke first).

    Mirrors the resolution order docstring above. Returns instance_ids
    in resolution order. Empty list means "no agents resolved" — the
    caller leaves the conversation as operator-only for that turn.
    """
    # Path 1 — explicit addressing.
    if addressed_to:
        # Dedupe preserving order; trust the caller's intent.
        return list(dict.fromkeys(addressed_to))

    # Build the name → id map ONCE — it's reused below + by callers
    # walking the chain.
    name_to_id = _build_name_to_id(participants, agent_lookup_fn)

    # Path 2 — @mentions in the body.
    mentioned = parse_mentions(body, name_to_id)
    if mentioned:
        return mentioned

    # Path 3 — fallback. Y3 keeps simple: first agent participant.
    # Y3.5 / Y4 may integrate suggest_agent.v1 here.
    if participants:
        return [participants[0].instance_id]
    return []


def resolve_chain_continuation(
    *,
    last_responder_id: str,
    last_response_body: str,
    participants:      list[Any],
    agent_lookup_fn:   Callable[[str], Any],
) -> list[str]:
    """After an agent's response, resolve who (if anyone) speaks next.

    Reads @mentions from the agent's body. Filters out the responder
    itself (no self-pass). Returns instance_ids in mention order or
    empty list (chain ends naturally).

    A future Y4 may extend this with topic-shift heuristics; v1 keeps
    it strictly @mention-driven so the operator can predict and
    audit the chain.
    """
    name_to_id = _build_name_to_id(participants, agent_lookup_fn)
    mentioned = parse_mentions(last_response_body, name_to_id)
    return [iid for iid in mentioned if iid != last_responder_id]


def _build_name_to_id(
    participants: list[Any],
    agent_lookup_fn: Callable[[str], Any],
) -> dict[str, str]:
    """Build {agent_name → instance_id} for the room's participants.

    Best-effort: agents whose lookup fails (registry inconsistency)
    are skipped silently — they can't be @mentioned but the chain
    continues for the rest.
    """
    name_to_id: dict[str, str] = {}
    for p in participants:
        try:
            agent = agent_lookup_fn(p.instance_id)
            if agent is not None and getattr(agent, "agent_name", None):
                name_to_id[agent.agent_name] = p.instance_id
        except Exception:
            continue
    return name_to_id
