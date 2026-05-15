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
