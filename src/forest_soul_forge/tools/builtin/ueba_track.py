"""``ueba_track.v1`` — per-user behavioral fingerprint over time.

ADR-0033 Phase B2. AnomalyAce + Investigator's per-user view: take
a stream of events with a ``user`` field and one or more activity
features, partition by user + a rolling time window, and emit a
fingerprint per user that can be diff'd across windows.

Differs from ``behavioral_baseline.v1`` in two ways:

  1. Per-user grouping is intrinsic — caller doesn't need a
     separate correlate step.
  2. Sliding-window output: the operator picks a window size
     (e.g. 1 hour) and the tool emits per-user, per-window stats
     so anomaly_score can compare last hour to the rolling
     prior-N-hours baseline.

side_effects=read_only — pure function over caller-supplied
events.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)
from forest_soul_forge.tools.builtin.behavioral_baseline import _parse_timestamp


_MAX_EVENTS = 100000
_MAX_USERS = 5000
_WINDOW_SIZES = ("hour", "day", "week")


class UebaTrackTool:
    """Per-user fingerprints across time windows.

    Args:
      events (list[dict], required): each must have ``user`` (str)
                                      and ``timestamp`` (ISO 8601
                                      or epoch).
      features (list[str], optional): extra fields to count distinct
                                       values of per (user, window).
                                       Default: ``["action"]`` if
                                       present in events, else [].
      window (str, optional): "hour" (default) | "day" | "week".

    Output:
      {
        "user_count":     int,
        "window_count":   int,
        "window":         str,
        "users": {
          "<user>": {
            "windows": [
              {
                "window_start": str,    # ISO
                "event_count":  int,
                "features":     {<name>: {"distinct": int, "top": [...]}},
                ...
              }, ...
            ],
            "total_events": int,
            "active_windows": int,
          }, ...
        }
      }
    """

    name = "ueba_track"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        events = args.get("events")
        if not isinstance(events, list):
            raise ToolValidationError("events must be a list of dicts")
        if len(events) > _MAX_EVENTS:
            raise ToolValidationError(
                f"events must be ≤ {_MAX_EVENTS}; got {len(events)}"
            )
        for i, e in enumerate(events):
            if not isinstance(e, dict):
                raise ToolValidationError(f"events[{i}] must be a dict")
        window = args.get("window")
        if window is not None and window not in _WINDOW_SIZES:
            raise ToolValidationError(
                f"window must be one of {list(_WINDOW_SIZES)}; got {window!r}"
            )
        feats = args.get("features")
        if feats is not None:
            if not isinstance(feats, list) or not all(isinstance(f, str) for f in feats):
                raise ToolValidationError("features must be a list of strings")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        events: list[dict] = args["events"]
        window = args.get("window") or "hour"
        features: list[str] = args.get("features") or ["action"]
        delta = {
            "hour": timedelta(hours=1),
            "day":  timedelta(days=1),
            "week": timedelta(weeks=1),
        }[window]

        # Bucket events into (user, window_start_str) cells.
        cells: dict[tuple, list[dict]] = defaultdict(list)
        skipped = 0
        for e in events:
            user = e.get("user")
            if not isinstance(user, str) or not user:
                skipped += 1
                continue
            ts = _parse_timestamp(e.get("timestamp"))
            if ts is None:
                skipped += 1
                continue
            window_start = _floor_to_window(ts, window)
            cells[(user, window_start)].append(e)

        # Per-user roll-ups.
        users: dict[str, dict] = {}
        per_user_windows: dict[str, list[dict]] = defaultdict(list)
        for (user, win_start), bucket in cells.items():
            feat_stats = {}
            for fname in features:
                values = [e.get(fname) for e in bucket if e.get(fname) is not None]
                str_values = [v if isinstance(v, str) else repr(v) for v in values]
                from collections import Counter
                ctr = Counter(str_values)
                feat_stats[fname] = {
                    "distinct": len(ctr),
                    "top":      [
                        {"value": v, "count": c}
                        for v, c in ctr.most_common(10)
                    ],
                }
            per_user_windows[user].append({
                "window_start": win_start,
                "event_count":  len(bucket),
                "features":     feat_stats,
            })

        # Cap user count for output (sorted by total activity desc).
        sorted_users = sorted(
            per_user_windows.items(),
            key=lambda kv: -sum(w["event_count"] for w in kv[1]),
        )
        truncated = len(sorted_users) > _MAX_USERS
        sorted_users = sorted_users[:_MAX_USERS]

        for user, wins in sorted_users:
            wins_sorted = sorted(wins, key=lambda w: w["window_start"])
            users[user] = {
                "windows":         wins_sorted,
                "total_events":    sum(w["event_count"] for w in wins_sorted),
                "active_windows":  len(wins_sorted),
            }

        all_windows = sorted({c[1] for c in cells.keys()})

        return ToolResult(
            output={
                "user_count":   len(users),
                "window_count": len(all_windows),
                "window":       window,
                "skipped":      skipped,
                "truncated":    truncated,
                "users":        users,
            },
            metadata={
                "features":     features,
                "events_total": len(events),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"{len(users)} users × {len(all_windows)} {window}-windows"
            ),
        )


def _floor_to_window(ts: datetime, window: str) -> str:
    """Floor a UTC datetime to the start of its window. Returns ISO 8601."""
    ts = ts.astimezone(timezone.utc)
    if window == "hour":
        floored = ts.replace(minute=0, second=0, microsecond=0)
    elif window == "day":
        floored = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    elif window == "week":
        # Floor to Monday 00:00 UTC.
        days_since_monday = ts.weekday()
        floored = (ts - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
    else:
        floored = ts
    return floored.strftime("%Y-%m-%dT%H:%M:%SZ")
