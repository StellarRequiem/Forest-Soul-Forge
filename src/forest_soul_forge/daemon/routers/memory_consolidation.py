"""``/memory/consolidation/*`` router — ADR-0074 T5 (B308) operator surface.

Read-only inspection on top of the B294-B307 substrate. Three endpoints:

  - **GET /memory/consolidation/status** — Pending count, summary count,
    last run id (from the audit chain), basic policy info. Drives the
    operator's "is consolidation healthy?" answer.

  - **GET /memory/consolidation/recent-summaries** — Last N summary
    entries (state='summary') with their source counts. Drives the
    "what did the last few runs produce?" view.

  - **POST /memory/consolidation/pin/{entry_id}** — Flip a memory
    entry to consolidation_state='pinned'. Operator protection
    surface — pinned entries never auto-consolidate.

  - **POST /memory/consolidation/unpin/{entry_id}** — Flip a pinned
    entry back to 'pending' so it's eligible again.

Pin/unpin are state mutations; gated by ``require_writes_enabled +
require_api_token`` like all write endpoints. The selector at B302
already filters by state='pending' so pinning is the right knob —
no separate "skip list" needed.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    require_api_token,
    require_writes_enabled,
)


router = APIRouter(prefix="/memory/consolidation", tags=["memory"])


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    dependencies=[Depends(require_api_token)],
)
def consolidation_status(
    request: Request,
    registry=Depends(get_registry),
    audit=Depends(get_audit_chain),
) -> dict[str, Any]:
    """Top-level consolidation health summary.

    Returns counts of memory_entries grouped by consolidation_state
    + the run_id of the most recent
    ``memory_consolidation_run_completed`` event (if any). Operators
    use this to answer "did the last run succeed?" and "how many
    rows are eligible right now?"
    """
    # Connection access uses the same SLF001-tolerated pattern as
    # passport/reality_anchor — the registry is the unified entry
    # point but doesn't yet expose a public conn shim.
    conn = registry._conn  # noqa: SLF001

    # State counts in a single grouped query.
    rows = conn.execute(
        "SELECT consolidation_state, COUNT(*) "
        "FROM memory_entries "
        "WHERE deleted_at IS NULL "
        "GROUP BY consolidation_state"
    ).fetchall()
    counts = {state: int(n) for state, n in rows}
    # Ensure every state appears even at zero so the frontend
    # can index without defensive checks.
    for state in ("pending", "consolidated", "summary", "pinned", "purged"):
        counts.setdefault(state, 0)

    # Pull the most recent run_completed event from the audit chain.
    # The chain tail iterator is the cheapest path here.
    last_run_id: str | None = None
    last_run_completed_at: str | None = None
    last_run_summary_count: int = 0
    last_run_source_count: int = 0
    try:
        for entry in _recent_chain_entries(audit, event_type=(
            "memory_consolidation_run_completed",
        ), limit=1):
            payload = entry.get("event_data", {})
            last_run_id = payload.get("run_id")
            last_run_completed_at = payload.get("completed_at")
            last_run_summary_count = int(
                payload.get("summaries_created", 0),
            )
            last_run_source_count = int(
                payload.get("entries_consolidated", 0),
            )
            break
    except Exception:
        # Chain unreachable / parse error — surface nulls rather
        # than 500. The state counts are still accurate.
        pass

    return {
        "schema_version": 1,
        "counts_by_state": counts,
        "last_run": {
            "run_id":            last_run_id,
            "completed_at":      last_run_completed_at,
            "summaries_created": last_run_summary_count,
            "entries_consolidated": last_run_source_count,
        },
    }


@router.get(
    "/recent-summaries",
    dependencies=[Depends(require_api_token)],
)
def recent_summaries(
    limit: int = Query(20, ge=1, le=200),
    registry=Depends(get_registry),
) -> dict[str, Any]:
    """Last N summary rows + how many sources each absorbed.

    Drives the operator's "what did consolidation produce lately?"
    view. Joins on `consolidated_into` to count children per
    summary.
    """
    conn = registry._conn  # noqa: SLF001
    cur = conn.execute(
        "SELECT s.entry_id, s.instance_id, s.layer, s.created_at, "
        "       s.consolidation_run, "
        "       (SELECT COUNT(*) FROM memory_entries c "
        "        WHERE c.consolidated_into = s.entry_id) AS source_count "
        "FROM memory_entries s "
        "WHERE s.consolidation_state = 'summary' "
        "ORDER BY s.created_at DESC "
        "LIMIT ?",
        (limit,),
    )
    summaries = [
        {
            "entry_id":     row[0],
            "instance_id":  row[1],
            "layer":        row[2],
            "created_at":   row[3],
            "run_id":       row[4],
            "source_count": int(row[5] or 0),
        }
        for row in cur.fetchall()
    ]
    return {"schema_version": 1, "count": len(summaries), "summaries": summaries}


# ---------------------------------------------------------------------------
# Writes — pin / unpin
# ---------------------------------------------------------------------------


@router.post(
    "/pin/{entry_id}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def pin_entry(
    entry_id: str,
    registry=Depends(get_registry),
) -> dict[str, Any]:
    """Flip a memory entry to ``consolidation_state='pinned'``.

    Pinned entries never auto-consolidate. The selector (B302)
    already filters by state='pending', so pinning is the right
    knob for "operator wants this preserved verbatim."

    Refuses (409) if the entry is already a summary or already
    consolidated — those states have specific lineage semantics
    and flipping out of them is a separate operator action (not
    in scope for T5).
    """
    return _flip_state(
        registry, entry_id,
        from_states=("pending",),
        to_state="pinned",
    )


@router.post(
    "/unpin/{entry_id}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
def unpin_entry(
    entry_id: str,
    registry=Depends(get_registry),
) -> dict[str, Any]:
    """Flip a pinned entry back to 'pending' so it's eligible
    for the next consolidation pass."""
    return _flip_state(
        registry, entry_id,
        from_states=("pinned",),
        to_state="pending",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flip_state(
    registry,
    entry_id: str,
    *,
    from_states: tuple[str, ...],
    to_state: str,
) -> dict[str, Any]:
    """Conditional UPDATE: flips state iff the row currently sits
    in one of `from_states`. Returns 404 on missing, 409 on
    wrong-state, 200 on success."""
    conn = registry._conn  # noqa: SLF001
    cur = conn.execute(
        "SELECT consolidation_state FROM memory_entries WHERE entry_id = ?",
        (entry_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no memory entry with id {entry_id!r}",
        )
    current = row[0]
    if current not in from_states:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"entry {entry_id!r} is in state {current!r}, "
                f"not in {list(from_states)} — refuse to flip to {to_state!r}"
            ),
        )
    with conn:
        conn.execute(
            "UPDATE memory_entries SET consolidation_state = ? "
            "WHERE entry_id = ?",
            (to_state, entry_id),
        )
    return {
        "ok":               True,
        "entry_id":         entry_id,
        "previous_state":   current,
        "consolidation_state": to_state,
    }


def _recent_chain_entries(
    audit,
    *,
    event_type: tuple[str, ...],
    limit: int,
    search_window: int = 2000,
):
    """Yield the most recent N entries matching `event_type`.

    Uses :meth:`AuditChain.tail` (same deque-based streaming reader
    that ``/audit/tail`` + the reality-anchor pane lean on — O(window)
    memory regardless of chain size). Reality-anchor's _read_recent_
    anchor_events sets the canonical pattern; we mirror it. Returns
    newest-first.

    Duck-typed audit interface so tests can pass a mock with a
    ``.tail(n)`` method or a ``.read_all()`` fallback.
    """
    if audit is None:
        return
    tail_fn = getattr(audit, "tail", None)
    if callable(tail_fn):
        try:
            entries = tail_fn(search_window)
        except Exception:
            return
        seen = 0
        for e in entries:  # newest-first
            et = getattr(e, "event_type", None)
            if et is None and isinstance(e, dict):
                et = e.get("event_type")
            if et in event_type:
                # Normalize to dict shape for the caller.
                if isinstance(e, dict):
                    yield e
                else:
                    yield {
                        "event_type": e.event_type,
                        "event_data": e.event_data,
                    }
                seen += 1
                if seen >= limit:
                    return
        return
    # Fallback: scan via read_all when tail isn't present.
    read_all = getattr(audit, "read_all", None)
    if callable(read_all):
        matches = [
            e for e in read_all()
            if (e.get("event_type") if isinstance(e, dict)
                else getattr(e, "event_type", None)) in event_type
        ]
        for e in reversed(matches[-limit:]):
            if isinstance(e, dict):
                yield e
            else:
                yield {
                    "event_type": e.event_type,
                    "event_data": e.event_data,
                }
