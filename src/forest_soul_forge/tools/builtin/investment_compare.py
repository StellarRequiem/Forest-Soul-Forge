"""``investment_compare.v1`` — ADR-0092 Phase C investment comparator.

Deterministic side-by-side comparison composer over operator-supplied
investment option records. Normalizes per-option fields, computes
delta-to-best per numeric dimension, and flags missing-data gaps so
the operator can refine the option corpus.

NEVER advises which to pick. The deliverable is the comparison
table; the operator decides. Same info-only discipline as the
manifest's investment_research capability + the constitution's
forbid_investment_advice policy.

Read-only. The ``investment_research.v1`` skill wraps this tool
with memory_recall (recent research briefs + prior option corpora)
+ memory_write (the comparison attestation); LLM-driven narrative
context layers separately.

## Comparison model

Three dimension classes per option:

- **lower_is_better** numeric (e.g. ``expense_ratio_pct``,
  ``front_load_pct``): for each, the option with the minimum
  value wins on that dimension; per-option delta is the value
  minus the minimum.
- **higher_is_better** numeric (e.g. ``one_year_return_pct``,
  ``five_year_return_pct``, ``ten_year_return_pct``,
  ``yield_pct``): for each, the option with the maximum value
  wins; per-option delta is the maximum minus the value.
- **info_only** (e.g. ``ticker``, ``name``, ``asset_class``,
  ``holdings_count``): surfaced for context; no delta computed.

Operator declares which fields to compare via the
``dimensions`` argument. Each dimension is one of the
above classes; missing values per option don't disqualify the
option, they're surfaced as ``missing_data`` in that option's
record so the operator sees the gap.

The tool never returns "the best option" — it returns per-
dimension winners + per-option deltas. Composing a verdict is
the operator's responsibility, not the tool's. This is the
explicit refusal of the "advisor-that-decides" anti-pattern.

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


_MAX_OPTIONS = 50
_MAX_DIMENSIONS = 30
_MAX_SLUG_LEN = 200
_MAX_FIELD_LEN = 500
_VALID_CLASSES = {"lower_is_better", "higher_is_better", "info_only"}


class InvestmentCompareTool:
    """Compose a deterministic side-by-side investment comparison.

    Args:
      comparison_slug (str, required): identifier this comparison
        binds to (e.g. ``index-funds-2026-05``). Recorded in
        output.
      options (list[dict], required): per-option records. Each
        entry:

          - ``option_slug`` (str, required): unique slug.
          - ``label`` (str, optional): display name.
          - ``fields`` (dict, required): operator-supplied
            per-field values. Field names must overlap with the
            ``dimensions`` array for those fields to participate.
      dimensions (list[dict], required): operator-supplied
        comparison dimensions. Each entry:

          - ``field`` (str, required): field name in options[*].fields.
          - ``class`` (str, required): one of lower_is_better /
            higher_is_better / info_only.
          - ``label`` (str, optional): display name for the
            attestation.

    Output:
      {
        "generated_at":     str (ISO),
        "comparison_slug":  str,
        "dimension_count":  int,
        "option_count":     int,
        "dimensions": [{
          "field":           str,
          "class":           str,
          "label":           str,
          "winner_slug":     str | null,        # null when info_only or all-missing
          "winner_value":    float | str | null,
          "rationale":       str,
        }, ...],
        "options": [{
          "option_slug":     str,
          "label":           str,
          "per_dimension": [{
            "field":             str,
            "class":             str,
            "value":             float | str | null,
            "delta_to_winner":   float | null,
            "is_winner":         bool,
            "missing_data":      bool,
          }, ...],
          "missing_data_count": int,
        }, ...],
        "summary": {
          "compared_dimension_count":    int,   # lower_is_better + higher_is_better
          "info_only_dimension_count":   int,
          "total_missing_data_cells":    int,
        },
      }
    """

    name = "investment_compare"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        slug = args.get("comparison_slug")
        if not isinstance(slug, str) or not slug.strip():
            raise ToolValidationError(
                "comparison_slug must be a non-empty string"
            )
        if len(slug) > _MAX_SLUG_LEN:
            raise ToolValidationError(
                f"comparison_slug must be <= {_MAX_SLUG_LEN} chars"
            )

        options = args.get("options")
        if not isinstance(options, list):
            raise ToolValidationError("options must be a list")
        if not options:
            raise ToolValidationError(
                "options must contain at least one entry"
            )
        if len(options) > _MAX_OPTIONS:
            raise ToolValidationError(
                f"options must have <= {_MAX_OPTIONS} entries; "
                f"got {len(options)}"
            )

        seen: set[str] = set()
        for i, entry in enumerate(options):
            if not isinstance(entry, dict):
                raise ToolValidationError(
                    f"options[{i}] must be a dict"
                )
            os_ = entry.get("option_slug")
            if not isinstance(os_, str) or not os_.strip():
                raise ToolValidationError(
                    f"options[{i}].option_slug must be a non-empty string"
                )
            if len(os_) > _MAX_SLUG_LEN:
                raise ToolValidationError(
                    f"options[{i}].option_slug must be <= {_MAX_SLUG_LEN} chars"
                )
            if os_ in seen:
                raise ToolValidationError(
                    f"options[{i}].option_slug duplicates earlier entry: {os_!r}"
                )
            seen.add(os_)
            label = entry.get("label")
            if label is not None:
                if not isinstance(label, str):
                    raise ToolValidationError(
                        f"options[{i}].label must be a string"
                    )
                if len(label) > _MAX_FIELD_LEN:
                    raise ToolValidationError(
                        f"options[{i}].label must be <= {_MAX_FIELD_LEN} chars"
                    )
            fields = entry.get("fields")
            if not isinstance(fields, dict):
                raise ToolValidationError(
                    f"options[{i}].fields must be a dict"
                )

        dims = args.get("dimensions")
        if not isinstance(dims, list):
            raise ToolValidationError("dimensions must be a list")
        if not dims:
            raise ToolValidationError(
                "dimensions must contain at least one entry"
            )
        if len(dims) > _MAX_DIMENSIONS:
            raise ToolValidationError(
                f"dimensions must have <= {_MAX_DIMENSIONS} entries; "
                f"got {len(dims)}"
            )

        seen_fields: set[str] = set()
        for i, dim in enumerate(dims):
            if not isinstance(dim, dict):
                raise ToolValidationError(
                    f"dimensions[{i}] must be a dict"
                )
            field = dim.get("field")
            if not isinstance(field, str) or not field.strip():
                raise ToolValidationError(
                    f"dimensions[{i}].field must be a non-empty string"
                )
            if len(field) > _MAX_FIELD_LEN:
                raise ToolValidationError(
                    f"dimensions[{i}].field must be <= {_MAX_FIELD_LEN} chars"
                )
            if field in seen_fields:
                raise ToolValidationError(
                    f"dimensions[{i}].field duplicates earlier entry: {field!r}"
                )
            seen_fields.add(field)
            cls = dim.get("class")
            if cls not in _VALID_CLASSES:
                raise ToolValidationError(
                    f"dimensions[{i}].class must be one of "
                    f"lower_is_better / higher_is_better / info_only"
                )
            lbl = dim.get("label")
            if lbl is not None:
                if not isinstance(lbl, str):
                    raise ToolValidationError(
                        f"dimensions[{i}].label must be a string"
                    )
                if len(lbl) > _MAX_FIELD_LEN:
                    raise ToolValidationError(
                        f"dimensions[{i}].label must be <= {_MAX_FIELD_LEN} chars"
                    )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        slug = args["comparison_slug"]
        options = args["options"]
        dims = args["dimensions"]

        dim_results: list[dict[str, Any]] = []
        winner_per_field: dict[str, tuple[str | None, float | None]] = {}
        for dim in dims:
            field = dim["field"]
            cls = dim["class"]
            label = dim.get("label") or field

            if cls == "info_only":
                dim_results.append({
                    "field":         field,
                    "class":         cls,
                    "label":         label,
                    "winner_slug":   None,
                    "winner_value":  None,
                    "rationale":     "info_only dimension; no winner computed.",
                })
                winner_per_field[field] = (None, None)
                continue

            candidates: list[tuple[str, float]] = []
            for opt in options:
                raw = opt["fields"].get(field)
                if (
                    isinstance(raw, (int, float))
                    and not isinstance(raw, bool)
                ):
                    candidates.append((opt["option_slug"], float(raw)))

            if not candidates:
                dim_results.append({
                    "field":         field,
                    "class":         cls,
                    "label":         label,
                    "winner_slug":   None,
                    "winner_value":  None,
                    "rationale":     "no numeric values present across options; no winner.",
                })
                winner_per_field[field] = (None, None)
                continue

            if cls == "lower_is_better":
                w_slug, w_val = min(
                    candidates, key=lambda c: (c[1], c[0])
                )
                rationale = (
                    f"lower_is_better: minimum value {w_val:.4f} "
                    f"held by {w_slug!r}."
                )
            else:  # higher_is_better
                w_slug, w_val = max(
                    candidates, key=lambda c: (c[1], -ord(c[0][:1] or "a"))
                )
                # ties: max by value; if tied, alphabetically-first slug wins
                ties = [c for c in candidates if c[1] == w_val]
                if len(ties) > 1:
                    w_slug = sorted(t[0] for t in ties)[0]
                rationale = (
                    f"higher_is_better: maximum value {w_val:.4f} "
                    f"held by {w_slug!r}."
                )

            dim_results.append({
                "field":         field,
                "class":         cls,
                "label":         label,
                "winner_slug":   w_slug,
                "winner_value":  round(w_val, 6),
                "rationale":     rationale,
            })
            winner_per_field[field] = (w_slug, w_val)

        option_results: list[dict[str, Any]] = []
        total_missing = 0
        for opt in options:
            per_dim: list[dict[str, Any]] = []
            missing = 0
            for dim in dims:
                field = dim["field"]
                cls = dim["class"]
                raw = opt["fields"].get(field)
                is_missing = False
                value_out: float | str | None = None
                delta: float | None = None
                is_winner = False
                if cls == "info_only":
                    if raw is None:
                        is_missing = True
                    elif isinstance(raw, (int, float, str)):
                        value_out = raw if isinstance(raw, str) else float(raw)
                    else:
                        is_missing = True
                else:
                    if (
                        isinstance(raw, (int, float))
                        and not isinstance(raw, bool)
                    ):
                        value_out = round(float(raw), 6)
                        w_slug, w_val = winner_per_field[field]
                        if w_val is not None:
                            if cls == "lower_is_better":
                                delta = round(float(raw) - w_val, 6)
                            else:
                                delta = round(w_val - float(raw), 6)
                            is_winner = (opt["option_slug"] == w_slug)
                    else:
                        is_missing = True
                if is_missing:
                    missing += 1
                per_dim.append({
                    "field":           field,
                    "class":           cls,
                    "value":           value_out,
                    "delta_to_winner": delta,
                    "is_winner":       is_winner,
                    "missing_data":    is_missing,
                })
            total_missing += missing
            option_results.append({
                "option_slug":          opt["option_slug"],
                "label":                opt.get("label") or opt["option_slug"],
                "per_dimension":        per_dim,
                "missing_data_count":   missing,
            })

        compared = sum(1 for d in dims if d["class"] != "info_only")
        info_only = sum(1 for d in dims if d["class"] == "info_only")

        summary = {
            "compared_dimension_count":     compared,
            "info_only_dimension_count":    info_only,
            "total_missing_data_cells":     total_missing,
        }

        body = {
            "generated_at":     datetime.now(timezone.utc)
                                       .replace(tzinfo=None)
                                       .isoformat(timespec="seconds")
                                       + "Z",
            "comparison_slug":  slug,
            "dimension_count":  len(dims),
            "option_count":     len(options),
            "dimensions":       dim_results,
            "options":          option_results,
            "summary":          summary,
        }

        return ToolResult(
            output=body,
            metadata={
                "comparison_slug":             slug,
                "option_count":                summary["compared_dimension_count"] and len(options) or len(options),
                "compared_dimension_count":    summary["compared_dimension_count"],
                "total_missing_data_cells":    summary["total_missing_data_cells"],
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"compared {len(options)} option"
                f"{'s' if len(options) != 1 else ''} "
                f"across {compared} numeric dimension"
                f"{'s' if compared != 1 else ''} "
                f"(info_only={info_only}; missing_cells={total_missing}); "
                f"NO recommendation issued (operator decides)"
            ),
        )
