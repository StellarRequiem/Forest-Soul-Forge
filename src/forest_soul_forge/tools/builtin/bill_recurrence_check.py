"""``bill_recurrence_check.v1`` — ADR-0092 Phase B bill recurrence detector.

Deterministic recurrence-pattern detection over a historical bill
ledger. For each bill series the tool detects monthly / quarterly
/ annual cycles within an operator-tolerable day_drift window,
flags missing-cycle anomalies, and projects the next-due date.

Read-only. The ``bill_management.v1`` skill wraps this tool with
memory_recall (recent bill ledgers + prior recurrence
attestations) + memory_write (the recurrence attestation);
LLM-driven due-date narrative is layered separately. The
d6→d2 cascade (Phase D) routes detected due-date attestations
into D2's schedule_reminder.v1 for fire-time delivery — D6
never queues a "pay the bill" action.

## Verdict model

For each distinct bill series (grouped by ``bill_slug``):

- **monthly**: consecutive bills are 28-32 days apart (default
  ``day_drift=3``); the tool projects next_due_iso = last_date +
  median_interval_days.
- **quarterly**: 88-92 days apart (default ``day_drift=3``).
- **annual**: 360-370 days apart (default ``day_drift=10``).
- **irregular**: bills exist but no cycle matches.
- **insufficient_history**: fewer than ``min_history`` bills in
  the series (default 2). No projection.

For series that match a cycle, the tool also computes a
``missing_cycle_flag`` — True when the gap between the last
bill date and today_iso exceeds ``median_interval_days +
day_drift``, meaning a bill that should have arrived is overdue.

side_effects=read_only.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_BILLS = 1000
_MAX_SLUG_LEN = 200
_MAX_FIELD_LEN = 500
_MIN_HISTORY_FLOOR = 2
_MIN_HISTORY_CEILING = 100
_DRIFT_FLOOR = 0
_DRIFT_CEILING = 60

_CYCLES = (
    ("monthly", 30, 3),
    ("quarterly", 91, 3),
    ("annual", 365, 10),
)


class BillRecurrenceCheckTool:
    """Detect recurrence patterns + project next-due dates over a bill ledger.

    Args:
      ledger_slug (str, required): identifier for this recurrence
        check (e.g. ``bills-2026-05``). Recorded in output.
      bills (list[dict], required): per-bill record across all
        series. The tool groups by ``bill_slug``. Each entry:

          - ``bill_slug`` (str, required): series identifier
            (e.g. ``rent``, ``netflix``, ``car-insurance``).
          - ``bill_date`` (str, required): ISO date (YYYY-MM-DD).
          - ``amount`` (number, optional): bill amount.
          - ``label`` (str, optional): human-readable name.
      today_iso (str, required): operator-supplied "today" anchor
        date (YYYY-MM-DD) for missing-cycle detection +
        next-due projection. The caller supplies this so the
        tool is deterministic + replayable.
      min_history (int, optional): minimum bills in a series to
        attempt cycle detection. Default 2.
      day_drift_overrides (dict, optional): per-cycle drift
        overrides. Keys: monthly / quarterly / annual.

    Output:
      {
        "generated_at":   str (ISO),
        "ledger_slug":    str,
        "today_iso":      str,
        "min_history":    int,
        "series": [{
          "bill_slug":            str,
          "label":                str,
          "bill_count":           int,
          "last_bill_date":       str | null,
          "median_interval_days": float | null,
          "verdict":              str,    # monthly / quarterly / annual / irregular / insufficient_history
          "next_due_iso":         str | null,
          "missing_cycle_flag":   bool,
          "rationale":            str,
        }, ...],
        "summary": {
          "series_count":             int,
          "matched_count":            int,
          "irregular_count":          int,
          "insufficient_count":       int,
          "missing_cycle_count":      int,
        },
      }
    """

    name = "bill_recurrence_check"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        slug = args.get("ledger_slug")
        if not isinstance(slug, str) or not slug.strip():
            raise ToolValidationError(
                "ledger_slug must be a non-empty string"
            )
        if len(slug) > _MAX_SLUG_LEN:
            raise ToolValidationError(
                f"ledger_slug must be <= {_MAX_SLUG_LEN} chars"
            )

        today = args.get("today_iso")
        if not isinstance(today, str) or not today.strip():
            raise ToolValidationError(
                "today_iso must be a non-empty ISO date string"
            )
        try:
            date.fromisoformat(today)
        except ValueError as exc:
            raise ToolValidationError(
                f"today_iso must be a YYYY-MM-DD date: {exc}"
            )

        bills = args.get("bills")
        if not isinstance(bills, list):
            raise ToolValidationError("bills must be a list")
        if not bills:
            raise ToolValidationError(
                "bills must contain at least one entry"
            )
        if len(bills) > _MAX_BILLS:
            raise ToolValidationError(
                f"bills must have <= {_MAX_BILLS} entries; "
                f"got {len(bills)}"
            )

        for i, entry in enumerate(bills):
            if not isinstance(entry, dict):
                raise ToolValidationError(
                    f"bills[{i}] must be a dict"
                )
            bs = entry.get("bill_slug")
            if not isinstance(bs, str) or not bs.strip():
                raise ToolValidationError(
                    f"bills[{i}].bill_slug must be a non-empty string"
                )
            if len(bs) > _MAX_SLUG_LEN:
                raise ToolValidationError(
                    f"bills[{i}].bill_slug must be <= {_MAX_SLUG_LEN} chars"
                )
            bd = entry.get("bill_date")
            if not isinstance(bd, str):
                raise ToolValidationError(
                    f"bills[{i}].bill_date must be a YYYY-MM-DD string"
                )
            try:
                date.fromisoformat(bd)
            except ValueError as exc:
                raise ToolValidationError(
                    f"bills[{i}].bill_date must be a YYYY-MM-DD date: {exc}"
                )
            amt = entry.get("amount")
            if amt is not None:
                if not isinstance(amt, (int, float)) or isinstance(amt, bool):
                    raise ToolValidationError(
                        f"bills[{i}].amount must be a number"
                    )
            label = entry.get("label")
            if label is not None:
                if not isinstance(label, str):
                    raise ToolValidationError(
                        f"bills[{i}].label must be a string"
                    )
                if len(label) > _MAX_FIELD_LEN:
                    raise ToolValidationError(
                        f"bills[{i}].label must be <= {_MAX_FIELD_LEN} chars"
                    )

        mh = args.get("min_history")
        if mh is not None:
            if (
                not isinstance(mh, int)
                or isinstance(mh, bool)
                or mh < _MIN_HISTORY_FLOOR
                or mh > _MIN_HISTORY_CEILING
            ):
                raise ToolValidationError(
                    f"min_history must be an integer in "
                    f"[{_MIN_HISTORY_FLOOR}, {_MIN_HISTORY_CEILING}]"
                )

        overrides = args.get("day_drift_overrides")
        if overrides is not None:
            if not isinstance(overrides, dict):
                raise ToolValidationError(
                    "day_drift_overrides must be a dict"
                )
            for k, v in overrides.items():
                if k not in ("monthly", "quarterly", "annual"):
                    raise ToolValidationError(
                        f"day_drift_overrides keys must be one of "
                        f"monthly / quarterly / annual; got {k!r}"
                    )
                if (
                    not isinstance(v, int)
                    or isinstance(v, bool)
                    or v < _DRIFT_FLOOR
                    or v > _DRIFT_CEILING
                ):
                    raise ToolValidationError(
                        f"day_drift_overrides.{k} must be an integer in "
                        f"[{_DRIFT_FLOOR}, {_DRIFT_CEILING}]"
                    )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        slug = args["ledger_slug"]
        bills = args["bills"]
        today = date.fromisoformat(args["today_iso"])
        min_history = int(args.get("min_history") or 2)
        overrides = args.get("day_drift_overrides") or {}

        cycles = []
        for name, days, default_drift in _CYCLES:
            drift = int(overrides.get(name, default_drift))
            cycles.append((name, days, drift))

        grouped: dict[str, list[dict[str, Any]]] = {}
        labels: dict[str, str] = {}
        for entry in bills:
            grouped.setdefault(entry["bill_slug"], []).append(entry)
            if entry.get("label"):
                labels.setdefault(entry["bill_slug"], entry["label"])

        series: list[dict[str, Any]] = []
        matched_count = 0
        irregular_count = 0
        insufficient_count = 0
        missing_cycle_count = 0

        for bill_slug in sorted(grouped.keys()):
            entries = sorted(
                grouped[bill_slug],
                key=lambda e: date.fromisoformat(e["bill_date"]),
            )
            dates = [date.fromisoformat(e["bill_date"]) for e in entries]
            last_date = dates[-1]

            verdict = "insufficient_history"
            median_interval: float | None = None
            next_due: str | None = None
            missing_flag = False
            rationale = (
                f"only {len(entries)} bill in series; need "
                f"min_history={min_history} to detect a cycle."
            )

            if len(dates) >= min_history:
                intervals = [
                    (dates[i] - dates[i - 1]).days
                    for i in range(1, len(dates))
                ]
                median_interval = float(median(intervals))
                matched: tuple[str, int, int] | None = None
                for name, days, drift in cycles:
                    if abs(median_interval - days) <= drift:
                        matched = (name, days, drift)
                        break
                if matched is not None:
                    name, days, drift = matched
                    verdict = name
                    next_due_date = last_date + timedelta(days=int(median_interval))
                    next_due = next_due_date.isoformat()
                    days_since_last = (today - last_date).days
                    if days_since_last > median_interval + drift:
                        missing_flag = True
                        rationale = (
                            f"{name} cycle (median_interval={median_interval:.1f}d "
                            f"± drift={drift}d); last bill on {last_date.isoformat()} "
                            f"was {days_since_last}d ago, exceeds median+drift; "
                            f"projected next_due {next_due}."
                        )
                    else:
                        rationale = (
                            f"{name} cycle (median_interval={median_interval:.1f}d "
                            f"± drift={drift}d); last bill on {last_date.isoformat()}; "
                            f"projected next_due {next_due}."
                        )
                    matched_count += 1
                else:
                    verdict = "irregular"
                    rationale = (
                        f"no cycle matched (median_interval={median_interval:.1f}d) "
                        f"against monthly/quarterly/annual ± drift."
                    )
                    irregular_count += 1
            else:
                insufficient_count += 1

            if missing_flag:
                missing_cycle_count += 1

            series.append({
                "bill_slug":             bill_slug,
                "label":                 labels.get(bill_slug, bill_slug),
                "bill_count":            len(entries),
                "last_bill_date":        last_date.isoformat(),
                "median_interval_days":  round(median_interval, 2) if median_interval is not None else None,
                "verdict":               verdict,
                "next_due_iso":          next_due,
                "missing_cycle_flag":    missing_flag,
                "rationale":             rationale,
            })

        summary = {
            "series_count":         len(series),
            "matched_count":        matched_count,
            "irregular_count":      irregular_count,
            "insufficient_count":   insufficient_count,
            "missing_cycle_count":  missing_cycle_count,
        }

        body = {
            "generated_at":  datetime.now(timezone.utc)
                                     .replace(tzinfo=None)
                                     .isoformat(timespec="seconds")
                                     + "Z",
            "ledger_slug":   slug,
            "today_iso":     args["today_iso"],
            "min_history":   min_history,
            "series":        series,
            "summary":       summary,
        }

        return ToolResult(
            output=body,
            metadata={
                "ledger_slug":           slug,
                "series_count":          summary["series_count"],
                "matched_count":         summary["matched_count"],
                "missing_cycle_count":   summary["missing_cycle_count"],
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"checked {summary['series_count']} bill series "
                f"(matched={summary['matched_count']}, "
                f"missing_cycle={summary['missing_cycle_count']})"
            ),
        )
