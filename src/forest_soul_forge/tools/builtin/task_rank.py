"""``task_rank.v1`` — ADR-0087 Phase C deterministic task ranker.

Takes an operator-provided task list + optional operator-profile
context, applies an urgency / impact / effort scoring model, and
returns a ranked list with per-task scores + a recommended order.

Read-only. No LLM in the tool itself — the
``task_prioritization.v1`` skill wraps this with ``llm_think`` for
the narrative explanation, but the ranking ITSELF is
deterministic so the operator can audit + replay it. LLM-driven
ranking would be opaque + unrepeatable; deterministic ranking
with explicit weights is what the value-prop calls for.

## Scoring model

Each task carries optional fields:

- ``urgency``  (int 0..10, default 5): how time-sensitive
- ``impact``   (int 0..10, default 5): expected leverage / value
- ``effort``   (int 0..10, default 5): how much energy required
- ``due_in_hours`` (int, optional): override urgency from a
  deadline; if present, urgency is recomputed as ``max(0, 10 -
  due_in_hours / 6.0)`` and capped at 10.
- ``tags`` (list[str], optional): if any tag matches an
  operator area_of_focus, the task gets a +1 focus bonus.

The composite score is:

    score = urgency_weight * urgency
          + impact_weight  * impact
          - effort_weight  * effort
          + focus_bonus

Default weights: urgency=1.2, impact=1.5, effort=0.5. Operators
can override per-call. Effort SUBTRACTS so cheaper-to-complete
high-impact tasks rise. Higher score = higher priority.

Returns the ranked list plus per-task score breakdown so the
operator can audit which dimension drove each rank.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_DEFAULT_WEIGHTS = {
    "urgency": 1.2,
    "impact":  1.5,
    "effort":  0.5,
}
_MAX_TASKS = 200
_MAX_TITLE_LEN = 500
_DIMENSION_MIN = 0
_DIMENSION_MAX = 10


class TaskRankTool:
    """Rank an operator-provided task list by urgency / impact / effort.

    Args:
      tasks (list[dict], required): one entry per task. Each entry
        has at minimum a ``title`` (string). Optional fields:
        ``urgency`` / ``impact`` / ``effort`` (int 0..10),
        ``due_in_hours`` (int >= 0; overrides urgency if present),
        ``tags`` (list[str]).
      weights (dict, optional): override default scoring weights.
        Keys: urgency / impact / effort. Each must be a non-negative
        number.
      areas_of_focus (list[str], optional): operator stated areas.
        Tasks tagged with any matching slug get a focus_bonus.
      focus_bonus (float, optional): score added when a tag
        matches an area. Default 1.0; non-negative.

    Output:
      {
        "generated_at":  str (ISO),
        "task_count":    int,
        "ranked":        [{
          "rank":          int,    # 1-based
          "title":         str,
          "score":         float,
          "breakdown":     {
            "urgency":      float,
            "impact":       float,
            "effort":       float,
            "focus_bonus":  float,
          },
          "tags":          [str, ...],
          "due_in_hours":  int | None,
        }, ...],
        "weights":       {urgency: float, impact: float, effort: float},
        "focus_bonus_value": float,
        "areas_of_focus": [str, ...],
      }
    """

    name = "task_rank"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        tasks = args.get("tasks")
        if not isinstance(tasks, list):
            raise ToolValidationError("tasks must be a list")
        if not tasks:
            raise ToolValidationError("tasks must contain at least one entry")
        if len(tasks) > _MAX_TASKS:
            raise ToolValidationError(
                f"tasks count must be <= {_MAX_TASKS}; got {len(tasks)}"
            )
        for i, t in enumerate(tasks):
            if not isinstance(t, dict):
                raise ToolValidationError(
                    f"tasks[{i}] must be a dict; got {type(t).__name__}"
                )
            title = t.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ToolValidationError(
                    f"tasks[{i}].title must be a non-empty string"
                )
            if len(title) > _MAX_TITLE_LEN:
                raise ToolValidationError(
                    f"tasks[{i}].title must be <= {_MAX_TITLE_LEN} chars"
                )
            for dim in ("urgency", "impact", "effort"):
                v = t.get(dim)
                if v is None:
                    continue
                if not isinstance(v, (int, float)):
                    raise ToolValidationError(
                        f"tasks[{i}].{dim} must be a number; "
                        f"got {type(v).__name__}"
                    )
                if v < _DIMENSION_MIN or v > _DIMENSION_MAX:
                    raise ToolValidationError(
                        f"tasks[{i}].{dim} must be in "
                        f"[{_DIMENSION_MIN}, {_DIMENSION_MAX}]; got {v}"
                    )
            due = t.get("due_in_hours")
            if due is not None:
                if not isinstance(due, (int, float)) or due < 0:
                    raise ToolValidationError(
                        f"tasks[{i}].due_in_hours must be a non-negative number"
                    )
            tags = t.get("tags", [])
            if not isinstance(tags, list):
                raise ToolValidationError(
                    f"tasks[{i}].tags must be a list"
                )
            for j, tag in enumerate(tags):
                if not isinstance(tag, str):
                    raise ToolValidationError(
                        f"tasks[{i}].tags[{j}] must be a string"
                    )

        weights = args.get("weights")
        if weights is not None:
            if not isinstance(weights, dict):
                raise ToolValidationError("weights must be a dict")
            for k in ("urgency", "impact", "effort"):
                if k not in weights:
                    continue
                v = weights[k]
                if not isinstance(v, (int, float)) or v < 0:
                    raise ToolValidationError(
                        f"weights.{k} must be a non-negative number"
                    )

        aof = args.get("areas_of_focus")
        if aof is not None:
            if not isinstance(aof, list):
                raise ToolValidationError(
                    "areas_of_focus must be a list of strings"
                )
            for j, s in enumerate(aof):
                if not isinstance(s, str):
                    raise ToolValidationError(
                        f"areas_of_focus[{j}] must be a string"
                    )

        fb = args.get("focus_bonus")
        if fb is not None:
            if not isinstance(fb, (int, float)) or fb < 0:
                raise ToolValidationError(
                    "focus_bonus must be a non-negative number"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        tasks = args["tasks"]
        weights = dict(_DEFAULT_WEIGHTS)
        if isinstance(args.get("weights"), dict):
            for k in ("urgency", "impact", "effort"):
                if k in args["weights"]:
                    weights[k] = float(args["weights"][k])
        areas_of_focus = list(args.get("areas_of_focus") or [])
        focus_bonus_value = float(args.get("focus_bonus") or 1.0)

        focus_set = set(s.lower() for s in areas_of_focus)
        scored: list[dict[str, Any]] = []
        for t in tasks:
            urgency = _dimension_or_default(t, "urgency")
            impact = _dimension_or_default(t, "impact")
            effort = _dimension_or_default(t, "effort")
            due_in_hours = t.get("due_in_hours")
            if isinstance(due_in_hours, (int, float)):
                # Tighter deadline -> higher urgency. Capped at 10.
                deadline_urgency = max(
                    0.0, 10.0 - float(due_in_hours) / 6.0,
                )
                urgency = min(10.0, max(urgency, deadline_urgency))
            tags = list(t.get("tags") or [])
            tag_set = set(s.lower() for s in tags)
            tag_match = bool(focus_set & tag_set)
            focus_bonus = focus_bonus_value if tag_match else 0.0

            score = (
                weights["urgency"] * urgency
                + weights["impact"] * impact
                - weights["effort"] * effort
                + focus_bonus
            )
            scored.append(
                {
                    "title":         t["title"],
                    "score":         round(score, 4),
                    "breakdown":     {
                        "urgency":      round(
                            weights["urgency"] * urgency, 4,
                        ),
                        "impact":       round(
                            weights["impact"] * impact, 4,
                        ),
                        "effort":       round(
                            weights["effort"] * effort, 4,
                        ),
                        "focus_bonus":  round(focus_bonus, 4),
                    },
                    "tags":          tags,
                    "due_in_hours":  due_in_hours,
                }
            )

        # Sort descending by score, breaking ties by original index
        # for stable, deterministic output.
        scored_with_idx = list(enumerate(scored))
        scored_with_idx.sort(
            key=lambda pair: (-pair[1]["score"], pair[0]),
        )
        ranked: list[dict[str, Any]] = []
        for rank, (_, entry) in enumerate(scored_with_idx, start=1):
            entry_with_rank = {"rank": rank, **entry}
            ranked.append(entry_with_rank)

        body = {
            "generated_at":      datetime.now(timezone.utc)
                                          .replace(tzinfo=None)
                                          .isoformat(timespec="seconds")
                                          + "Z",
            "task_count":        len(tasks),
            "ranked":            ranked,
            "weights":           weights,
            "focus_bonus_value": focus_bonus_value,
            "areas_of_focus":    areas_of_focus,
        }
        return ToolResult(
            output=body,
            metadata={
                "task_count":      len(tasks),
                "top_title":       ranked[0]["title"] if ranked else "",
                "top_score":       ranked[0]["score"] if ranked else 0.0,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"ranked {len(tasks)} tasks; top: "
                f"{ranked[0]['title'] if ranked else 'n/a'}"
            ),
        )


def _dimension_or_default(t: dict[str, Any], key: str) -> float:
    v = t.get(key)
    if v is None:
        return 5.0
    return float(v)
