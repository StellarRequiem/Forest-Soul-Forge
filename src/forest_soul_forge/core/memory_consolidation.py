"""Memory consolidation selector — ADR-0074 T2 (B302).

Pure-function candidate-batch selector on top of the B294 substrate
(``memory_entries.consolidation_state`` + ``consolidated_into`` +
``consolidation_run`` columns, schema v23).

The selector answers a single question: *"of all the pending
memory entries, which ones is the runner allowed to fold into a
summary on the next pass?"* It does not summarize, embed, or
write — those are T3+ concerns. Keeping selection pure makes the
policy testable in isolation against an in-memory SQLite, and
keeps T3-T5 free to compose the runner.

## Policy

:class:`ConsolidationPolicy` carries the operator-tunable knobs:

- ``min_age_days`` — entries newer than this stay
  ``pending`` regardless of other policy fields. Default 14 days.
  Rationale: very recent entries are still being acted on by the
  agent that wrote them; consolidating them prematurely loses the
  immediate-recall surface.
- ``max_batch_size`` — cap on rows returned per call. Default 200.
  Pairs with ADR-0075 budget caps: one consolidation pass produces
  one summary entry per agent per layer, so 200 sources is roughly
  one summary per dispatch.
- ``eligible_layers`` — only entries in these layers get
  consolidated. Default ``("episodic",)``. ``working`` entries are
  ephemeral by design; ``consolidated`` entries are already summaries.
- ``eligible_claim_types`` — only entries with these claim types.
  Default ``("observation", "user_statement")``. ``promise`` and
  ``preference`` are higher-stakes and should not auto-consolidate
  (ADR-0027 amendment §7); the runner's policy explicitly excludes
  them until an operator opt-in arrives.

## Filter logic (in SQL)

Selection conditions (all AND-combined):

  * ``consolidation_state = 'pending'``  — the explicit eligible state
  * ``deleted_at IS NULL``               — preserved-but-deleted excluded
  * ``layer IN eligible_layers``
  * ``claim_type IN eligible_claim_types``
  * ``created_at < cutoff_iso``          — age gate

Ordering: ``created_at ASC`` so the oldest pending entries
consolidate first. This is FIFO, which matches the operator
mental model ("first in, first folded").

Limit: ``max_batch_size``.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class ConsolidationPolicy:
    """Operator-tunable knobs for the consolidation selector."""

    min_age_days: int = 14
    max_batch_size: int = 200
    eligible_layers: tuple[str, ...] = ("episodic",)
    eligible_claim_types: tuple[str, ...] = (
        "observation",
        "user_statement",
    )

    def __post_init__(self) -> None:
        # Frozen dataclasses don't run __init__ validation by
        # default; we re-check here so invalid policies are caught
        # at construction, not at the SQL site.
        if self.min_age_days < 0:
            raise ValueError(
                f"min_age_days must be >= 0, got {self.min_age_days}"
            )
        if self.max_batch_size <= 0:
            raise ValueError(
                f"max_batch_size must be > 0, got {self.max_batch_size}"
            )
        if not self.eligible_layers:
            raise ValueError("eligible_layers cannot be empty")
        if not self.eligible_claim_types:
            raise ValueError("eligible_claim_types cannot be empty")


def _cutoff_iso(now: datetime, min_age_days: int) -> str:
    """Return the ISO-8601 string for `now - min_age_days`."""
    return (now - timedelta(days=min_age_days)).isoformat()


@dataclass(frozen=True)
class SourceEntry:
    """One memory_entry that's about to be rolled into a summary.

    The summarizer (T3) takes a list of these. Carries enough for
    the LLM prompt + the lineage record the runner (T4) will write
    when it flips state from pending to consolidated.
    """
    entry_id: str
    content: str
    layer: str
    claim_type: str
    created_at: str  # ISO 8601


@dataclass(frozen=True)
class SummaryDraft:
    """The output of one summarization pass.

    The runner (T4) consumes this draft + does the SQL writes
    atomically: insert a new `summary`-state memory_entry with
    ``content``, mark every ``source_entry_id`` as
    ``consolidated`` with ``consolidated_into`` pointing at the
    new summary, set ``consolidation_run`` on all touched rows.

    ``layer`` is forwarded from the sources — summaries inherit
    the layer of the entries they absorb (a batch of `episodic`
    entries produces an `episodic` summary). ``claim_type`` on a
    summary is always `agent_inference` because the summary IS
    an inference over the source observations — per ADR-0027
    amendment's epistemic-metadata rules.
    """
    content: str
    source_entry_ids: tuple[str, ...]
    layer: str
    claim_type: str = "agent_inference"
    # The prompt the LLM saw — kept so the audit chain emit can
    # carry a digest of the input for tamper-evidence.
    prompt: str = ""


class ConsolidationSummarizerError(RuntimeError):
    """Raised when the summarizer can't produce a usable draft.

    The runner (T4) treats this as a soft-fail: the batch stays
    pending for the next pass, no state mutation, no audit emit.
    A persistent failure surfaces via the scheduled-task circuit
    breaker (ADR-0041 max_consecutive_failures)."""


def _render_summary_prompt(sources: list[SourceEntry]) -> str:
    """Build the LLM prompt for consolidating a batch.

    Format is deliberately minimal: numbered observations, then a
    one-paragraph ask. The summarizer wants a faithful rollup —
    "what's the operator's pattern here?" — NOT analysis or
    advice. The runner doesn't post-process the output; whatever
    the LLM emits becomes the summary content verbatim.
    """
    lines = [
        "You are summarizing memory entries from a personal agent's"
        " history. Produce a single short paragraph (max 80 words)"
        " that captures the durable signal across these observations."
        " Preserve specifics that matter (people, times, decisions),"
        " drop incidentals. Do NOT add advice or analysis — just the"
        " summary.",
        "",
        f"Layer: {sources[0].layer}",
        "",
        "Observations:",
    ]
    for i, s in enumerate(sources, start=1):
        lines.append(f"  {i}. [{s.created_at}] {s.content}")
    lines.append("")
    lines.append("Summary:")
    return "\n".join(lines)


async def summarize_consolidation_batch(
    sources: list[SourceEntry],
    *,
    provider: Any,
    max_tokens: int = 200,
) -> SummaryDraft:
    """ADR-0074 T3: produce a SummaryDraft from a batch of sources.

    Pure with respect to the database — does not read or write
    memory_entries. The runner (T4) handles the SQL transactions.
    Calls ``provider.complete`` with TaskKind.GENERATE so the
    provider routes to its generation-sized model.

    Refuses if:
      - sources is empty (no batch to summarize)
      - any source has empty content (the LLM would hallucinate)
      - sources span multiple layers (the runner enforces single-
        layer batches; surfacing it here as a defensive guard
        catches a bug in selector composition)

    Returns a frozen SummaryDraft. The runner inspects
    ``source_entry_ids`` to know which rows to mark consolidated,
    and ``content`` to write into the new summary entry.
    """
    if not sources:
        raise ConsolidationSummarizerError("empty source batch")
    if any(not s.content.strip() for s in sources):
        raise ConsolidationSummarizerError(
            "one or more source entries has empty content"
        )
    layers = {s.layer for s in sources}
    if len(layers) > 1:
        raise ConsolidationSummarizerError(
            f"batch spans multiple layers: {sorted(layers)}"
        )

    prompt = _render_summary_prompt(sources)

    try:
        # Lazy import so the daemon-side TaskKind doesn't get
        # pulled into core. Test code uses a mock provider that
        # doesn't import TaskKind at all.
        from forest_soul_forge.daemon.providers.base import TaskKind
        task_kind = TaskKind.GENERATE
    except ImportError:
        task_kind = "generate"  # mock-friendly fallback

    try:
        summary_text = await provider.complete(
            prompt,
            task_kind=task_kind,
            max_tokens=max_tokens,
        )
    except Exception as e:
        raise ConsolidationSummarizerError(
            f"provider.complete failed: {type(e).__name__}: {e}"
        ) from e

    summary_text = (summary_text or "").strip()
    if not summary_text:
        raise ConsolidationSummarizerError(
            "provider returned an empty summary"
        )

    return SummaryDraft(
        content=summary_text,
        source_entry_ids=tuple(s.entry_id for s in sources),
        layer=sources[0].layer,
        claim_type="agent_inference",
        prompt=prompt,
    )


def select_consolidation_candidates(
    conn: sqlite3.Connection,
    *,
    policy: ConsolidationPolicy,
    now: Optional[datetime] = None,
) -> list[str]:
    """Return entry_ids of memory rows eligible for the next pass.

    Parameters
    ----------
    conn:
        Open SQLite connection to a registry whose schema is v23+
        (carries the B294 consolidation columns + partial indexes).
    policy:
        Filter + batch-size + age policy.
    now:
        Override the "current time" for age-window math. Defaults
        to ``datetime.now(timezone.utc)``; tests inject a fixed
        anchor so the cutoff is deterministic.

    Returns
    -------
    list[str]
        entry_ids in oldest-first order. Empty when no rows match.

    Notes
    -----
    The function does not mutate the table — that's T3's job.
    Returning a plain list (rather than a generator or row tuples)
    keeps the contract trivially testable + portable across
    callsites (a future API endpoint that lists candidates uses
    the same call).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = _cutoff_iso(now, policy.min_age_days)

    # IN-clause requires placeholders per element — build them.
    layer_marks = ", ".join("?" for _ in policy.eligible_layers)
    claim_marks = ", ".join("?" for _ in policy.eligible_claim_types)

    sql = (
        "SELECT entry_id FROM memory_entries WHERE "
        "consolidation_state = 'pending' "
        "AND deleted_at IS NULL "
        f"AND layer IN ({layer_marks}) "
        f"AND claim_type IN ({claim_marks}) "
        "AND created_at < ? "
        "ORDER BY created_at ASC "
        "LIMIT ?"
    )
    params: list = []
    params.extend(policy.eligible_layers)
    params.extend(policy.eligible_claim_types)
    params.append(cutoff)
    params.append(policy.max_batch_size)

    cur = conn.execute(sql, params)
    return [row[0] for row in cur.fetchall()]
