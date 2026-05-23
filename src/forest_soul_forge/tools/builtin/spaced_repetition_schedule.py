"""``spaced_repetition_schedule.v1`` — ADR-0089 Phase D scheduler.

Computes the next review interval for a topic via the SM-2 spaced-
repetition algorithm and appends a review-queue record to
``data/d9/review_queue.jsonl``. The fire-time delivery itself
runs unattended when D2's ``schedule_reminder.v1`` substrate picks
the queue up at the operator-approved fire time.

side_effects=filesystem. The actuator-genre external ceiling
permits this; ``filesystem_always_human_approval`` rule gates each
queue update so the operator approves before any review lands in
the ledger. Same two-layer pattern as time_steward's
schedule_reminder.v1 (filesystem) + YELLOW posture combination.

## SM-2 algorithm in one paragraph

Given a recall quality grade q ∈ [0..5] (0 = total blackout, 5 =
perfect recall):

  - Repetition counter ``n`` advances when q >= 3; resets to 0 when
    q < 3 (the operator has to start the schedule over).
  - Easiness factor ``EF`` updates by:
        EF' = max(1.3, EF + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
  - Next interval ``I`` (in days):
        n == 0:  I = 1
        n == 1:  I = 6
        n >= 2:  I = round(prev_interval * EF')

Default starting EF is 2.5. The tool returns the new EF +
next-interval-days + fire_at + reviewed_at so the operator can
audit the schedule pacing.

ADR-0089 Decision 3 — spaced_repetition_pilot (YELLOW posture)
is the only role with this in its kit. Composes with D2's
schedule_reminder.v1 — the queue record this tool writes is
distinct from the reminder ledger; a future forest-review
connector (or operator-driven workflow) picks the queue up at
fire_at and dispatches schedule_reminder.v1 if the operator
wants a reminder for it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_DEFAULT_QUEUE_PATH = Path("data/d9/review_queue.jsonl")
_MAX_TOPIC_LEN = 200
_DEFAULT_EF = 2.5
_MIN_EF = 1.3
_MAX_INTERVAL_DAYS = 365


class SpacedRepetitionScheduleTool:
    """Compute SM-2 next interval + append queue record.

    Args:
      topic_slug (str, required): topic being scheduled. Matches an
        item_id-bound topic from a prior assessment_score.v1 call.
      quality (int, required): recall quality grade in [0..5]
        (SM-2 convention; 5 = perfect, 0 = blackout).
      prior_repetition (int, optional): previous repetition counter
        ``n``. Default 0 (first review).
      prior_easiness (float, optional): previous easiness factor
        ``EF``. Default 2.5; clamped to >= 1.3.
      prior_interval_days (int, optional): previous interval in
        days. Default 0 (first review).
      reviewed_at (str, optional): ISO timestamp when the review
        happened. Default ``now`` (UTC, Pacific time per CLAUDE.md
        operator constraints — operators write zoned ISO).
      source_score_id (str, optional): assessment_score memory
        entry_id that triggered the schedule update. Lets the
        operator trace from queue back to score event.
      queue_path (str, optional): override queue path; tests pass
        a fixture path; production callers omit.

    Output:
      {
        "schedule_id":      str,        # stable derived id
        "topic_slug":       str,
        "quality":          int,
        "next_repetition":  int,        # n'
        "next_easiness":    float,      # EF'
        "next_interval_days": int,      # I'
        "fire_at":          str (ISO),  # reviewed_at + I' days
        "reviewed_at":      str (ISO),
        "queue_path":       str,
      }
    """

    name = "spaced_repetition_schedule"
    version = "1"
    side_effects = "filesystem"

    def validate(self, args: dict[str, Any]) -> None:
        slug = args.get("topic_slug")
        if not isinstance(slug, str) or not slug.strip():
            raise ToolValidationError("topic_slug is required")
        if len(slug) > _MAX_TOPIC_LEN:
            raise ToolValidationError(
                f"topic_slug must be <= {_MAX_TOPIC_LEN} chars"
            )

        q = args.get("quality")
        if not isinstance(q, int) or q < 0 or q > 5:
            raise ToolValidationError(
                "quality must be an integer in [0, 5] (SM-2 grade)"
            )

        rep = args.get("prior_repetition")
        if rep is not None:
            if not isinstance(rep, int) or rep < 0:
                raise ToolValidationError(
                    "prior_repetition must be a non-negative integer"
                )

        ef = args.get("prior_easiness")
        if ef is not None:
            if not isinstance(ef, (int, float)) or ef < _MIN_EF:
                raise ToolValidationError(
                    f"prior_easiness must be a number >= {_MIN_EF}"
                )

        ivl = args.get("prior_interval_days")
        if ivl is not None:
            if not isinstance(ivl, int) or ivl < 0:
                raise ToolValidationError(
                    "prior_interval_days must be a non-negative integer"
                )

        ra = args.get("reviewed_at")
        if ra is not None:
            if not isinstance(ra, str):
                raise ToolValidationError("reviewed_at must be a string")
            try:
                _parse_iso(ra)
            except ValueError as e:
                raise ToolValidationError(f"reviewed_at not parseable: {e}")

        for k in ("source_score_id", "queue_path"):
            v = args.get(k)
            if v is not None and not isinstance(v, str):
                raise ToolValidationError(f"{k} must be a string")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        slug = args["topic_slug"]
        q = int(args["quality"])
        prior_rep = int(args.get("prior_repetition") or 0)
        prior_ef = float(args.get("prior_easiness") or _DEFAULT_EF)
        prior_ivl = int(args.get("prior_interval_days") or 0)
        source_score_id = args.get("source_score_id") or ""

        if args.get("reviewed_at"):
            reviewed_dt = _parse_iso(args["reviewed_at"])
        else:
            reviewed_dt = datetime.now(timezone.utc)

        queue_path = Path(
            args.get("queue_path") or _DEFAULT_QUEUE_PATH
        )
        queue_path.parent.mkdir(parents=True, exist_ok=True)

        next_ef = _sm2_easiness(prior_ef, q)
        if q < 3:
            next_rep = 0
            next_ivl = 1
        else:
            next_rep = prior_rep + 1
            if next_rep == 1:
                next_ivl = 1
            elif next_rep == 2:
                next_ivl = 6
            else:
                next_ivl = min(
                    _MAX_INTERVAL_DAYS,
                    max(1, round(prior_ivl * next_ef)),
                )

        fire_dt = reviewed_dt + timedelta(days=next_ivl)
        ts = reviewed_dt.isoformat(timespec="seconds")
        schedule_id = _derive_id(slug, q, ts)

        record = {
            "schedule_id":         schedule_id,
            "topic_slug":          slug,
            "quality":             q,
            "next_repetition":     next_rep,
            "next_easiness":       round(next_ef, 4),
            "next_interval_days":  next_ivl,
            "fire_at":             fire_dt.isoformat(timespec="seconds"),
            "reviewed_at":         ts,
            "source_score_id":     source_score_id,
            "attestor":            ctx.instance_id,
            "agent_role":          ctx.role,
        }

        try:
            with queue_path.open("a") as f:
                f.write(json.dumps(record, sort_keys=True))
                f.write("\n")
        except OSError as e:
            raise ToolValidationError(
                f"could not append to review_queue {queue_path}: {e}"
            )

        body = {
            "schedule_id":         schedule_id,
            "topic_slug":          slug,
            "quality":             q,
            "next_repetition":     next_rep,
            "next_easiness":       round(next_ef, 4),
            "next_interval_days":  next_ivl,
            "fire_at":             fire_dt.isoformat(timespec="seconds"),
            "reviewed_at":         ts,
            "queue_path":          str(queue_path),
        }
        return ToolResult(
            output=body,
            metadata={
                "schedule_id":         schedule_id,
                "topic_slug":          slug,
                "next_interval_days":  next_ivl,
                "fire_at":             body["fire_at"],
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"scheduled review {schedule_id} for {slug!r}: "
                f"next in {next_ivl}d (EF={next_ef:.2f})"
            ),
        )


def _sm2_easiness(prior_ef: float, q: int) -> float:
    """SM-2 easiness factor update.

    EF' = EF + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    Clamped at MIN_EF (1.3).
    """
    diff = 5 - q
    delta = 0.1 - diff * (0.08 + diff * 0.02)
    new_ef = prior_ef + delta
    return max(_MIN_EF, new_ef)


def _parse_iso(s: str) -> datetime:
    raw = s.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _derive_id(slug: str, quality: int, ts: str) -> str:
    blob = f"{slug}|{quality}|{ts}"
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"sr_{digest[:12]}"
