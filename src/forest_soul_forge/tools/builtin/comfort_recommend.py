"""``comfort_recommend.v1`` — ADR-0091 Phase B comfort recommendation composer.

Deterministic per-area comfort-tuning recommendation generator. For
each area in the current home_state the tool computes a small set of
ordered recommendations across three dimensions (temperature,
lighting, scene) using an operator-supplied preference profile +
time-of-day window. The output is always one of a small set of
``action_kind`` enums so the comfort_optimizer's attestation is
diff'able across windows + the operator can audit which input drove
which recommendation.

Read-only. The ``comfort_tuning.v1`` skill wraps this tool with
memory_recall (recent home_state snapshots + recent recommendation
attestations) + memory_write (the new recommendation attestation);
LLM-driven narrative is layered separately.

## Recommendation model

Three dimensions per area, evaluated independently:

- **temperature**:
  - if ``current_temp_f`` is outside ``[preferred_temp_min_f,
    preferred_temp_max_f]`` window by >= ``temp_action_delta_f``
    (default 2°F): recommend ``adjust_temperature`` toward
    the window midpoint.
  - else if outside by < delta: ``no_action`` with
    "within tolerance" rationale.
  - else: ``no_action``.
- **lighting**: time-of-day-driven.
  - if window is ``evening`` or ``night`` AND
    ``current_brightness_pct`` > ``evening_dim_threshold_pct``
    (default 40): recommend ``dim_lights`` toward 30%.
  - if window is ``morning`` AND ``current_brightness_pct`` <
    ``morning_brighten_threshold_pct`` (default 50): recommend
    ``brighten_lights`` toward 70%.
  - else: ``no_action``.
- **scene**:
  - if ``vacation_mode`` is True AND ``current_scene`` != ``away``:
    recommend ``set_scene`` to ``away`` with high priority.
  - if ``current_scene`` is None or empty AND window is one of
    ``morning``/``evening``/``night``: recommend ``set_scene`` to
    the window-matching default.
  - else: ``no_action``.

The wrapping skill orders recommendations: scene-changes first
(highest scope), then temperature, then lighting. Within a dimension
the order is by area_slug for stability.

side_effects=read_only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_AREAS = 100
_MAX_SLUG_LEN = 200
_MAX_LABEL_LEN = 500
_VALID_WINDOWS = {"morning", "midday", "evening", "night"}
_WINDOW_DEFAULT_SCENE = {
    "morning":  "morning",
    "midday":   "default",
    "evening":  "evening",
    "night":    "night",
}


class ComfortRecommendTool:
    """Compose per-area comfort recommendations.

    Args:
      window_slug (str, required): time-bucket slug for the
        attestation. Recorded in output.
      time_of_day (str, required): one of ``morning`` / ``midday``
        / ``evening`` / ``night``. Drives lighting + scene defaults.
      areas (list[dict], required): per-area current state. Each
        entry:

          - ``area_slug`` (str, required): kebab-case identifier
          - ``label`` (str, optional): human-readable area name
          - ``current_temp_f`` (number, optional): current temp °F
          - ``current_brightness_pct`` (number, optional): 0..100
          - ``current_scene`` (str, optional): currently-active
            scene name
      preferences (dict, optional): operator's preference profile.

          - ``preferred_temp_min_f`` (number, default 68)
          - ``preferred_temp_max_f`` (number, default 74)
          - ``evening_dim_threshold_pct`` (number, default 40)
          - ``morning_brighten_threshold_pct`` (number, default 50)
          - ``temp_action_delta_f`` (number, default 2.0)
      vacation_mode (bool, optional): when True the scene
        dimension always recommends ``away``. Default False.

    Output:
      {
        "generated_at":     str (ISO),
        "window_slug":      str,
        "time_of_day":      str,
        "vacation_mode":    bool,
        "preferences":      dict (resolved with defaults),
        "recommendations": [{
          "area_slug":       str,
          "label":           str,
          "dimension":       str,        # temperature / lighting / scene
          "action_kind":     str,        # adjust_temperature / dim_lights / brighten_lights / set_scene / no_action
          "target":          float | str | null,
          "priority":        int,        # 1=highest..3
          "rationale":       str,
        }, ...],
        "summary": {
          "area_count":          int,
          "action_count":        int,    # excludes no_action
          "no_action_count":     int,
          "highest_priority":    int | null,
        },
      }
    """

    name = "comfort_recommend"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        slug = args.get("window_slug")
        if not isinstance(slug, str) or not slug.strip():
            raise ToolValidationError(
                "window_slug must be a non-empty string"
            )
        if len(slug) > _MAX_SLUG_LEN:
            raise ToolValidationError(
                f"window_slug must be <= {_MAX_SLUG_LEN} chars"
            )

        tod = args.get("time_of_day")
        if not isinstance(tod, str) or tod not in _VALID_WINDOWS:
            raise ToolValidationError(
                "time_of_day must be one of "
                "morning / midday / evening / night"
            )

        areas = args.get("areas")
        if not isinstance(areas, list):
            raise ToolValidationError("areas must be a list")
        if not areas:
            raise ToolValidationError(
                "areas must contain at least one entry"
            )
        if len(areas) > _MAX_AREAS:
            raise ToolValidationError(
                f"areas must have <= {_MAX_AREAS} entries; got {len(areas)}"
            )

        seen: set[str] = set()
        for i, entry in enumerate(areas):
            if not isinstance(entry, dict):
                raise ToolValidationError(
                    f"areas[{i}] must be a dict"
                )
            ds = entry.get("area_slug")
            if not isinstance(ds, str) or not ds.strip():
                raise ToolValidationError(
                    f"areas[{i}].area_slug must be a non-empty string"
                )
            if len(ds) > _MAX_SLUG_LEN:
                raise ToolValidationError(
                    f"areas[{i}].area_slug must be <= {_MAX_SLUG_LEN} chars"
                )
            if ds in seen:
                raise ToolValidationError(
                    f"areas[{i}].area_slug duplicates earlier entry: {ds!r}"
                )
            seen.add(ds)
            label = entry.get("label")
            if label is not None:
                if not isinstance(label, str):
                    raise ToolValidationError(
                        f"areas[{i}].label must be a string"
                    )
                if len(label) > _MAX_LABEL_LEN:
                    raise ToolValidationError(
                        f"areas[{i}].label must be <= {_MAX_LABEL_LEN} chars"
                    )
            t = entry.get("current_temp_f")
            if t is not None:
                if (
                    not isinstance(t, (int, float))
                    or isinstance(t, bool)
                ):
                    raise ToolValidationError(
                        f"areas[{i}].current_temp_f must be a number"
                    )
            b = entry.get("current_brightness_pct")
            if b is not None:
                if (
                    not isinstance(b, (int, float))
                    or isinstance(b, bool)
                    or b < 0
                    or b > 100
                ):
                    raise ToolValidationError(
                        f"areas[{i}].current_brightness_pct "
                        "must be a number in [0, 100]"
                    )
            scene = entry.get("current_scene")
            if scene is not None and not isinstance(scene, str):
                raise ToolValidationError(
                    f"areas[{i}].current_scene must be a string"
                )

        prefs = args.get("preferences")
        if prefs is not None:
            if not isinstance(prefs, dict):
                raise ToolValidationError(
                    "preferences must be a dict"
                )
            for k in (
                "preferred_temp_min_f",
                "preferred_temp_max_f",
                "evening_dim_threshold_pct",
                "morning_brighten_threshold_pct",
                "temp_action_delta_f",
            ):
                v = prefs.get(k)
                if v is not None:
                    if (
                        not isinstance(v, (int, float))
                        or isinstance(v, bool)
                    ):
                        raise ToolValidationError(
                            f"preferences.{k} must be a number"
                        )
            mn = prefs.get("preferred_temp_min_f")
            mx = prefs.get("preferred_temp_max_f")
            if mn is not None and mx is not None and mn > mx:
                raise ToolValidationError(
                    "preferences.preferred_temp_min_f must be "
                    "<= preferred_temp_max_f"
                )
            for k in (
                "evening_dim_threshold_pct",
                "morning_brighten_threshold_pct",
            ):
                v = prefs.get(k)
                if v is not None and (v < 0 or v > 100):
                    raise ToolValidationError(
                        f"preferences.{k} must be in [0, 100]"
                    )

        vm = args.get("vacation_mode")
        if vm is not None and not isinstance(vm, bool):
            raise ToolValidationError(
                "vacation_mode must be a boolean"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        slug = args["window_slug"]
        tod = args["time_of_day"]
        areas = args["areas"]
        prefs_in = args.get("preferences") or {}
        vacation_mode = bool(args.get("vacation_mode") or False)

        prefs = {
            "preferred_temp_min_f":             float(prefs_in.get("preferred_temp_min_f", 68)),
            "preferred_temp_max_f":             float(prefs_in.get("preferred_temp_max_f", 74)),
            "evening_dim_threshold_pct":        float(prefs_in.get("evening_dim_threshold_pct", 40)),
            "morning_brighten_threshold_pct":   float(prefs_in.get("morning_brighten_threshold_pct", 50)),
            "temp_action_delta_f":              float(prefs_in.get("temp_action_delta_f", 2.0)),
        }

        recs: list[dict[str, Any]] = []
        sorted_areas = sorted(areas, key=lambda a: a["area_slug"])

        # Scene first (highest scope).
        for entry in sorted_areas:
            recs.append(_scene_rec(entry, tod, vacation_mode))
        # Temperature second.
        for entry in sorted_areas:
            recs.append(_temp_rec(entry, prefs))
        # Lighting third.
        for entry in sorted_areas:
            recs.append(_lighting_rec(entry, tod, prefs))

        action_count = sum(1 for r in recs if r["action_kind"] != "no_action")
        no_action_count = len(recs) - action_count
        action_priorities = [
            r["priority"] for r in recs if r["action_kind"] != "no_action"
        ]
        highest_priority = min(action_priorities) if action_priorities else None

        summary = {
            "area_count":         len(sorted_areas),
            "action_count":       action_count,
            "no_action_count":    no_action_count,
            "highest_priority":   highest_priority,
        }

        body = {
            "generated_at":     datetime.now(timezone.utc)
                                              .replace(tzinfo=None)
                                              .isoformat(timespec="seconds")
                                              + "Z",
            "window_slug":      slug,
            "time_of_day":      tod,
            "vacation_mode":    vacation_mode,
            "preferences":      prefs,
            "recommendations":  recs,
            "summary":          summary,
        }

        return ToolResult(
            output=body,
            metadata={
                "window_slug":   slug,
                "area_count":    summary["area_count"],
                "action_count":  summary["action_count"],
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"composed {summary['action_count']} action"
                f"{'s' if summary['action_count'] != 1 else ''} "
                f"across {summary['area_count']} area"
                f"{'s' if summary['area_count'] != 1 else ''} "
                f"({tod}, vacation={'on' if vacation_mode else 'off'})"
            ),
        )


def _scene_rec(
    entry: dict[str, Any], tod: str, vacation_mode: bool,
) -> dict[str, Any]:
    area = entry["area_slug"]
    label = entry.get("label") or area
    current = entry.get("current_scene")
    if vacation_mode:
        if current != "away":
            return {
                "area_slug":   area,
                "label":       label,
                "dimension":   "scene",
                "action_kind": "set_scene",
                "target":      "away",
                "priority":    1,
                "rationale":   (
                    f"vacation_mode active; current scene "
                    f"{current!r} != 'away'."
                ),
            }
        return {
            "area_slug":   area,
            "label":       label,
            "dimension":   "scene",
            "action_kind": "no_action",
            "target":      None,
            "priority":    3,
            "rationale":   "vacation_mode active; already on 'away' scene.",
        }
    if not current:
        default = _WINDOW_DEFAULT_SCENE.get(tod, "default")
        return {
            "area_slug":   area,
            "label":       label,
            "dimension":   "scene",
            "action_kind": "set_scene",
            "target":      default,
            "priority":    2,
            "rationale":   (
                f"no current scene set; recommend window-default "
                f"{default!r} for time_of_day={tod}."
            ),
        }
    return {
        "area_slug":   area,
        "label":       label,
        "dimension":   "scene",
        "action_kind": "no_action",
        "target":      None,
        "priority":    3,
        "rationale":   f"current scene {current!r} retained.",
    }


def _temp_rec(
    entry: dict[str, Any], prefs: dict[str, float],
) -> dict[str, Any]:
    area = entry["area_slug"]
    label = entry.get("label") or area
    t = entry.get("current_temp_f")
    if t is None:
        return {
            "area_slug":   area,
            "label":       label,
            "dimension":   "temperature",
            "action_kind": "no_action",
            "target":      None,
            "priority":    3,
            "rationale":   "no current_temp_f reading available.",
        }
    t = float(t)
    lo = prefs["preferred_temp_min_f"]
    hi = prefs["preferred_temp_max_f"]
    delta = prefs["temp_action_delta_f"]
    midpoint = round((lo + hi) / 2.0, 2)
    if t < lo - delta:
        return {
            "area_slug":   area,
            "label":       label,
            "dimension":   "temperature",
            "action_kind": "adjust_temperature",
            "target":      midpoint,
            "priority":    2,
            "rationale":   (
                f"current {t:.1f}°F < preferred_min {lo:.1f}°F - "
                f"delta {delta:.1f}°F; adjust toward midpoint."
            ),
        }
    if t > hi + delta:
        return {
            "area_slug":   area,
            "label":       label,
            "dimension":   "temperature",
            "action_kind": "adjust_temperature",
            "target":      midpoint,
            "priority":    2,
            "rationale":   (
                f"current {t:.1f}°F > preferred_max {hi:.1f}°F + "
                f"delta {delta:.1f}°F; adjust toward midpoint."
            ),
        }
    if t < lo or t > hi:
        return {
            "area_slug":   area,
            "label":       label,
            "dimension":   "temperature",
            "action_kind": "no_action",
            "target":      None,
            "priority":    3,
            "rationale":   (
                f"current {t:.1f}°F outside preferred window "
                f"[{lo:.1f},{hi:.1f}]°F but within tolerance "
                f"delta={delta:.1f}°F."
            ),
        }
    return {
        "area_slug":   area,
        "label":       label,
        "dimension":   "temperature",
        "action_kind": "no_action",
        "target":      None,
        "priority":    3,
        "rationale":   (
            f"current {t:.1f}°F within preferred window "
            f"[{lo:.1f},{hi:.1f}]°F."
        ),
    }


def _lighting_rec(
    entry: dict[str, Any], tod: str, prefs: dict[str, float],
) -> dict[str, Any]:
    area = entry["area_slug"]
    label = entry.get("label") or area
    b = entry.get("current_brightness_pct")
    if b is None:
        return {
            "area_slug":   area,
            "label":       label,
            "dimension":   "lighting",
            "action_kind": "no_action",
            "target":      None,
            "priority":    3,
            "rationale":   "no current_brightness_pct reading available.",
        }
    b = float(b)
    if tod in ("evening", "night"):
        thr = prefs["evening_dim_threshold_pct"]
        if b > thr:
            return {
                "area_slug":   area,
                "label":       label,
                "dimension":   "lighting",
                "action_kind": "dim_lights",
                "target":      30.0,
                "priority":    2,
                "rationale":   (
                    f"time_of_day={tod}; current brightness {b:.0f}% > "
                    f"evening_dim_threshold {thr:.0f}%."
                ),
            }
        return {
            "area_slug":   area,
            "label":       label,
            "dimension":   "lighting",
            "action_kind": "no_action",
            "target":      None,
            "priority":    3,
            "rationale":   (
                f"time_of_day={tod}; brightness {b:.0f}% already "
                f"<= dim threshold."
            ),
        }
    if tod == "morning":
        thr = prefs["morning_brighten_threshold_pct"]
        if b < thr:
            return {
                "area_slug":   area,
                "label":       label,
                "dimension":   "lighting",
                "action_kind": "brighten_lights",
                "target":      70.0,
                "priority":    2,
                "rationale":   (
                    f"time_of_day=morning; current brightness {b:.0f}% < "
                    f"morning_brighten_threshold {thr:.0f}%."
                ),
            }
        return {
            "area_slug":   area,
            "label":       label,
            "dimension":   "lighting",
            "action_kind": "no_action",
            "target":      None,
            "priority":    3,
            "rationale":   (
                f"time_of_day=morning; brightness {b:.0f}% already "
                f">= brighten threshold."
            ),
        }
    return {
        "area_slug":   area,
        "label":       label,
        "dimension":   "lighting",
        "action_kind": "no_action",
        "target":      None,
        "priority":    3,
        "rationale":   f"time_of_day={tod}; lighting default policy.",
    }
