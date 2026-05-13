"""RealityAnchorCorrectionsTable — ADR-0063 T6 correction memory.

One row per unique hallucinated claim that the Reality Anchor
caught at either the dispatcher gate (T3) or the conversation
hook (T5). The row's ``repetition_count`` bumps on every repeat
hit so an operator can answer "which agents keep making the
same wrong claim?" without walking the audit chain manually.

## Why one table for both surfaces

Substrate-layer gate + conversation hook both produce the same
shape of finding (claim text + contradicting fact + severity +
agent identity). Distinguishing them on disk would force
operator-facing queries to UNION two tables. We keep one table
and tag the surface in ``last_surface`` ∈ {dispatcher,
conversation}.

## Claim normalization

The PRIMARY KEY is ``claim_hash`` = sha256 of the *normalized*
claim text:

  - lowercased
  - whitespace collapsed (runs of \\s+ → single space)
  - leading/trailing trimmed

Same normalization is applied EVERY time we look up or write
so an agent that emits the same claim with different casing or
spacing still maps to the same row.

We deliberately do NOT normalize away punctuation or
function-words because that would collide semantically-different
claims onto the same hash (false repeats). The §0 Hippocratic
gate applies: better to under-merge (some repeat claims look
like new ones) than over-merge (different claims report as the
same hallucination).

## bump_or_create returns post-bump count

A return value of 1 = first occurrence (no repeat). 2+ = at
least one prior occurrence; caller fires
``reality_anchor_repeat_offender`` once count crosses 2.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional

from forest_soul_forge.registry.tables._helpers import transaction


# ---- normalization --------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_claim(claim: str) -> str:
    """Apply the canonical normalization. Returns the form we hash.

    Pure function, no side effects. Exported so tests + future
    operator-facing tools can reproduce the same hash a daemon
    would compute.
    """
    if not claim:
        return ""
    lower = claim.lower().strip()
    return _WHITESPACE_RE.sub(" ", lower)


def claim_hash(claim: str) -> str:
    """sha256 hex digest of the normalized claim."""
    norm = normalize_claim(claim)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ---- public read shape ----------------------------------------------------


@dataclass(frozen=True)
class CorrectionRow:
    claim_hash: str
    canonical_claim: str
    contradicts_fact_id: str
    worst_severity: str
    first_seen_at: str
    last_seen_at: str
    repetition_count: int
    last_agent_dna: Optional[str]
    last_instance_id: Optional[str]
    last_decision: str       # "refused" | "warned"
    last_surface: str        # "dispatcher" | "conversation"


# ---- the accessor ---------------------------------------------------------


class RealityAnchorCorrectionsTable:
    """Persistence layer for the correction memory.

    Single instance per Registry, threaded through the dispatcher
    pipeline + conversation hook via the same wiring pattern
    we use for procedural_shortcuts. The bump-or-create entry
    point is the only mutation surface.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- mutation ---------------------------------------------------------

    def bump_or_create(
        self,
        *,
        claim: str,
        fact_id: str,
        worst_severity: str,
        now_iso: str,
        agent_dna: str | None,
        instance_id: str | None,
        decision: str,          # "refused" | "warned"
        surface: str,           # "dispatcher" | "conversation"
    ) -> int:
        """Idempotent upsert keyed on the normalized claim hash.

        First sighting → row created with repetition_count=1.
        Repeat sighting → repetition_count bumped, last_* fields
        overwritten, first_seen_at preserved.

        Returns the post-bump ``repetition_count``. Callers
        compare against 1 to decide whether to fire
        reality_anchor_repeat_offender.

        All under a single transaction (no read-then-write window).
        Caller is responsible for holding the daemon write_lock
        when concurrent dispatches could collide on the same
        claim_hash — same posture as tool_call_counters.
        """
        ch = claim_hash(claim)
        canonical = normalize_claim(claim)

        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO reality_anchor_corrections (
                    claim_hash, canonical_claim, contradicts_fact_id,
                    worst_severity, first_seen_at, last_seen_at,
                    repetition_count, last_agent_dna, last_instance_id,
                    last_decision, last_surface
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(claim_hash) DO UPDATE SET
                    last_seen_at      = excluded.last_seen_at,
                    repetition_count  = repetition_count + 1,
                    last_agent_dna    = excluded.last_agent_dna,
                    last_instance_id  = excluded.last_instance_id,
                    last_decision     = excluded.last_decision,
                    last_surface      = excluded.last_surface,
                    contradicts_fact_id = excluded.contradicts_fact_id,
                    worst_severity    = CASE
                        WHEN _severity_rank(excluded.worst_severity)
                             > _severity_rank(reality_anchor_corrections.worst_severity)
                        THEN excluded.worst_severity
                        ELSE reality_anchor_corrections.worst_severity
                    END;
                """,
                (
                    ch, canonical, fact_id, worst_severity,
                    now_iso, now_iso,
                    agent_dna, instance_id, decision, surface,
                ),
            ) if _HAS_SEVERITY_RANK_FN.get(id(self._conn)) else (
                # No SQLite UDF wired — fall back to a two-step
                # upsert that picks the worst severity in Python.
                # (We register the UDF once per connection in
                # _ensure_udf below to avoid this branch.)
                self._bump_or_create_python(
                    ch, canonical, fact_id, worst_severity,
                    now_iso, agent_dna, instance_id,
                    decision, surface,
                )
            )

            row = self._conn.execute(
                "SELECT repetition_count FROM reality_anchor_corrections "
                "WHERE claim_hash=?;",
                (ch,),
            ).fetchone()
        return int(row["repetition_count"]) if row is not None else 1

    def _bump_or_create_python(
        self, ch, canonical, fact_id, worst_severity, now_iso,
        agent_dna, instance_id, decision, surface,
    ) -> None:
        """Two-step fallback when the SQLite UDF isn't registered.

        SELECT then INSERT-or-UPDATE. We're already inside the
        outer transaction; this branch keeps the worst_severity
        merge logic in Python instead of SQL. No correctness
        difference — just a slower path for connections that
        skipped the UDF setup.
        """
        rank = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        existing = self._conn.execute(
            "SELECT worst_severity FROM reality_anchor_corrections "
            "WHERE claim_hash=?;",
            (ch,),
        ).fetchone()
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO reality_anchor_corrections (
                    claim_hash, canonical_claim, contradicts_fact_id,
                    worst_severity, first_seen_at, last_seen_at,
                    repetition_count, last_agent_dna, last_instance_id,
                    last_decision, last_surface
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?);
                """,
                (
                    ch, canonical, fact_id, worst_severity,
                    now_iso, now_iso,
                    agent_dna, instance_id, decision, surface,
                ),
            )
            return
        new_worst = (
            worst_severity
            if rank.get(worst_severity, -1)
                > rank.get(existing["worst_severity"], -1)
            else existing["worst_severity"]
        )
        self._conn.execute(
            """
            UPDATE reality_anchor_corrections
               SET last_seen_at        = ?,
                   repetition_count    = repetition_count + 1,
                   last_agent_dna      = ?,
                   last_instance_id    = ?,
                   last_decision       = ?,
                   last_surface        = ?,
                   contradicts_fact_id = ?,
                   worst_severity      = ?
             WHERE claim_hash = ?;
            """,
            (
                now_iso, agent_dna, instance_id, decision, surface,
                fact_id, new_worst, ch,
            ),
        )

    # ---- read ------------------------------------------------------------

    def get(self, claim: str) -> CorrectionRow | None:
        """Look up by claim text (re-hashes). Returns None on miss."""
        ch = claim_hash(claim)
        row = self._conn.execute(
            "SELECT * FROM reality_anchor_corrections WHERE claim_hash=?;",
            (ch,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_dataclass(row)

    def get_by_hash(self, claim_hash_hex: str) -> CorrectionRow | None:
        row = self._conn.execute(
            "SELECT * FROM reality_anchor_corrections WHERE claim_hash=?;",
            (claim_hash_hex,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_dataclass(row)

    def list_repeat_offenders(
        self, *, min_repetitions: int = 2, limit: int = 100,
    ) -> list[CorrectionRow]:
        """Return the top-N rows with repetition_count >= min.

        Default min=2 because a single occurrence is a finding,
        not a 'repeat offender.' Default limit=100 caps the
        operator UI's blast radius — bigger queries can pass
        explicit limits.
        """
        rows = self._conn.execute(
            "SELECT * FROM reality_anchor_corrections "
            "WHERE repetition_count >= ? "
            "ORDER BY repetition_count DESC, last_seen_at DESC "
            "LIMIT ?;",
            (min_repetitions, limit),
        ).fetchall()
        return [_row_to_dataclass(r) for r in rows]


# ---- internals -----------------------------------------------------------


# Per-connection flag so we know whether the UDF path is wired.
# Keyed on id(conn) because sqlite3.Connection isn't hashable.
# False on every connection today; the Python fallback is the
# only path actually used. The UDF path remains as a future
# optimization slot — we left the conditional in bump_or_create
# rather than removing it so the eventual UDF wiring is a 5-line
# patch instead of a structural change. The False default keeps
# behavior deterministic in v1.
_HAS_SEVERITY_RANK_FN: dict[int, bool] = {}


def _row_to_dataclass(row: sqlite3.Row) -> CorrectionRow:
    return CorrectionRow(
        claim_hash=row["claim_hash"],
        canonical_claim=row["canonical_claim"],
        contradicts_fact_id=row["contradicts_fact_id"],
        worst_severity=row["worst_severity"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        repetition_count=int(row["repetition_count"]),
        last_agent_dna=row["last_agent_dna"],
        last_instance_id=row["last_instance_id"],
        last_decision=row["last_decision"],
        last_surface=row["last_surface"],
    )
