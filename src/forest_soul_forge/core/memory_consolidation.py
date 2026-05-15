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

import hashlib
import logging
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# ADR-0074 T4 (B307) — consolidation runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsolidationRunResult:
    """Aggregate outcome of one consolidation pass.

    ``run_id`` is the UUID stamped on every row + audit event the
    pass touched — operators trace any consolidated entry back to
    its run via the column or the audit chain.

    ``batches_processed`` counts (instance_id, layer) groups that
    completed successfully. ``sources_consolidated`` is the total
    source-row count across all groups (so a 50-source group +
    a 12-source group reports 62 here).

    ``errors`` is a list of (instance_id, layer, error_message)
    tuples — per-group soft failures (LLM returned empty, multi-
    layer batch composition bug, etc.). The pass continues past
    a group failure; persistent failure surfaces via the scheduled-
    task circuit breaker.
    """
    run_id: str
    started_at: str  # ISO 8601 UTC
    completed_at: str  # ISO 8601 UTC
    batches_processed: int
    summaries_created: int
    sources_consolidated: int
    errors: tuple[tuple[str, str, str], ...]


def _fetch_source_entries(
    conn: sqlite3.Connection,
    entry_ids: list[str],
) -> list[tuple[str, str, str, str, str, str]]:
    """Read source rows for a batch. Returns tuples of
    (entry_id, instance_id, content, layer, claim_type, created_at).

    Skips rows whose ``content_encrypted=1`` because the runner
    holds no decryption key — those entries stay pending and are
    surfaced as a soft skip. (Encrypted memory consolidation
    queues for a later tranche when the runner has key access.)
    """
    if not entry_ids:
        return []
    marks = ", ".join("?" for _ in entry_ids)
    sql = (
        "SELECT entry_id, instance_id, content, layer, claim_type, created_at "
        "FROM memory_entries "
        f"WHERE entry_id IN ({marks}) "
        "AND consolidation_state = 'pending' "
        "AND deleted_at IS NULL "
        "AND content_encrypted = 0"
    )
    cur = conn.execute(sql, entry_ids)
    return cur.fetchall()


def _group_by_instance_and_layer(
    rows: list[tuple[str, str, str, str, str, str]],
) -> dict[tuple[str, str], list[SourceEntry]]:
    """Partition rows by (instance_id, layer). The summarizer
    contract requires single-layer batches; one summary per
    (agent, layer) keeps the lineage clean — an agent's episodic
    memories don't fold into another agent's summary."""
    groups: dict[tuple[str, str], list[SourceEntry]] = defaultdict(list)
    for entry_id, instance_id, content, layer, claim_type, created_at in rows:
        groups[(instance_id, layer)].append(SourceEntry(
            entry_id=entry_id,
            content=content,
            layer=layer,
            claim_type=claim_type,
            created_at=created_at,
        ))
    return dict(groups)


def _content_digest(text: str) -> str:
    """SHA-256 of the canonical content bytes. Matches the
    ``content_digest`` column convention on memory_entries —
    operators correlating a summary row to its audit emit see
    the same digest in both places."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def run_consolidation_pass(
    conn: sqlite3.Connection,
    *,
    policy: ConsolidationPolicy,
    provider: Any,
    audit_chain: Any,
    agent_dna_for_summary: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ConsolidationRunResult:
    """ADR-0074 T4: run one end-to-end consolidation pass.

    Steps:

    1. Mint a run_id (UUID4 hex).
    2. Emit ``memory_consolidation_run_started`` carrying the
       selector_window + candidate_count.
    3. Pull candidates via :func:`select_consolidation_candidates`.
    4. Fetch source content rows; group by (instance_id, layer).
    5. For each group:
        a. Call :func:`summarize_consolidation_batch`.
        b. In a single SQL transaction:
            - INSERT the summary row (state='summary',
              consolidation_run=run_id, claim_type='agent_inference').
            - UPDATE every source row: state='consolidated',
              consolidated_into=<new_summary_id>, consolidation_run=run_id.
        c. Emit ``memory_consolidated`` per source row.
       A summarizer error logs + accumulates in errors; the
       pass continues. A SQL error rolls back THAT group's
       transaction and accumulates in errors.
    6. Emit ``memory_consolidation_run_completed`` carrying
       totals + errors + wall-clock duration.

    The function does NOT acquire any lock — the caller (the
    scheduled task in T5) holds Forest's write_lock per single-
    writer discipline.

    Returns :class:`ConsolidationRunResult` with the run-level
    totals + per-group errors.
    """
    started_dt = now or datetime.now(timezone.utc)
    started_iso = started_dt.isoformat()
    run_id = uuid.uuid4().hex

    candidate_ids = select_consolidation_candidates(
        conn, policy=policy, now=started_dt,
    )

    _emit(audit_chain, "memory_consolidation_run_started", {
        "run_id":           run_id,
        "started_at":       started_iso,
        "selector_window": {
            "min_age_days":         policy.min_age_days,
            "max_batch_size":       policy.max_batch_size,
            "eligible_layers":      list(policy.eligible_layers),
            "eligible_claim_types": list(policy.eligible_claim_types),
        },
        "instance_filter":  None,
        "candidate_count":  len(candidate_ids),
    })

    if not candidate_ids:
        # Empty pass — still emit run_completed so the chain
        # carries a complete bookend pair.
        completed_iso = datetime.now(timezone.utc).isoformat()
        _emit(audit_chain, "memory_consolidation_run_completed", {
            "run_id":                 run_id,
            "completed_at":           completed_iso,
            "entries_consolidated":   0,
            "summaries_created":      0,
            "errors":                 [],
            "wall_clock_ms":          _wall_clock_ms(started_dt),
        })
        return ConsolidationRunResult(
            run_id=run_id, started_at=started_iso,
            completed_at=completed_iso,
            batches_processed=0, summaries_created=0,
            sources_consolidated=0, errors=(),
        )

    rows = _fetch_source_entries(conn, candidate_ids)
    groups = _group_by_instance_and_layer(rows)

    batches_processed = 0
    summaries_created = 0
    sources_consolidated = 0
    errors: list[tuple[str, str, str]] = []

    for (instance_id, layer), sources in groups.items():
        try:
            draft = await summarize_consolidation_batch(
                sources, provider=provider,
            )
        except ConsolidationSummarizerError as e:
            logger.warning(
                "consolidation run %s: group (%s, %s) summarize failed: %s",
                run_id, instance_id, layer, e,
            )
            errors.append((instance_id, layer, f"summarize: {e}"))
            continue

        # SQL section: atomic per group. Use `with conn:` — sqlite3's
        # connection-as-context-manager commits on clean exit and
        # rolls back on exception, which is exactly what we want here.
        # Don't issue explicit BEGIN/COMMIT in SQL: sqlite3's Python
        # driver already manages an implicit transaction and the two
        # collide ("cannot start a transaction within a transaction").
        summary_id = uuid.uuid4().hex
        try:
            with conn:
                conn.execute(
                    "INSERT INTO memory_entries ("
                    "  entry_id, instance_id, agent_dna, layer, scope, "
                    "  content, content_digest, tags_json, "
                    "  consented_to_json, created_at, claim_type, "
                    "  confidence, content_encrypted, "
                    "  consolidation_state, consolidation_run"
                    ") VALUES (?, ?, ?, ?, 'private', ?, ?, '[]', '[]', "
                    "?, 'agent_inference', 'medium', 0, "
                    "'summary', ?)",
                    (
                        summary_id,
                        instance_id,
                        agent_dna_for_summary or "consolidation_runner",
                        layer,
                        draft.content,
                        _content_digest(draft.content),
                        started_iso,
                        run_id,
                    ),
                )
                # Flip every source. Single UPDATE with IN clause —
                # minimizes round trips and keeps the transaction narrow.
                id_marks = ", ".join("?" for _ in draft.source_entry_ids)
                conn.execute(
                    "UPDATE memory_entries SET "
                    "consolidation_state = 'consolidated', "
                    "consolidated_into = ?, "
                    "consolidation_run = ? "
                    f"WHERE entry_id IN ({id_marks})",
                    [summary_id, run_id, *draft.source_entry_ids],
                )
        except sqlite3.Error as e:
            # `with conn` already rolled back; no manual ROLLBACK needed.
            logger.exception(
                "consolidation run %s: group (%s, %s) SQL failed",
                run_id, instance_id, layer,
            )
            errors.append((instance_id, layer, f"sql: {e}"))
            continue

        # Audit emits AFTER the transaction commits — emitting
        # mid-transaction would couple chain hash to SQL state
        # in a way that's hard to recover from.
        for src in sources:
            _emit(audit_chain, "memory_consolidated", {
                "run_id":            run_id,
                "source_entry_id":   src.entry_id,
                "summary_entry_id":  summary_id,
                "layer":             src.layer,
                "claim_type":        src.claim_type,
            })

        batches_processed += 1
        summaries_created += 1
        sources_consolidated += len(sources)

    completed_iso = datetime.now(timezone.utc).isoformat()
    _emit(audit_chain, "memory_consolidation_run_completed", {
        "run_id":                run_id,
        "completed_at":          completed_iso,
        "entries_consolidated":  sources_consolidated,
        "summaries_created":     summaries_created,
        "errors":                [list(e) for e in errors],
        "wall_clock_ms":         _wall_clock_ms(started_dt),
    })

    return ConsolidationRunResult(
        run_id=run_id,
        started_at=started_iso,
        completed_at=completed_iso,
        batches_processed=batches_processed,
        summaries_created=summaries_created,
        sources_consolidated=sources_consolidated,
        errors=tuple(errors),
    )


def _emit(audit_chain: Any, event_type: str, payload: dict) -> None:
    """Best-effort audit emit. Never raises out of the runner —
    same posture as Scheduler._emit_audit. The chain is the
    evidence layer; if it's down, the runner still does its job
    and the operator sees the gap on chain inspection."""
    if audit_chain is None:
        return
    try:
        audit_chain.append(event_type, payload, agent_dna=None)
    except Exception:
        logger.exception(
            "consolidation runner audit emit failed for %s", event_type,
        )


def _wall_clock_ms(started: datetime) -> float:
    """Milliseconds since `started`, rounded to 2 decimals."""
    return round(
        (datetime.now(timezone.utc) - started).total_seconds() * 1000.0,
        2,
    )
