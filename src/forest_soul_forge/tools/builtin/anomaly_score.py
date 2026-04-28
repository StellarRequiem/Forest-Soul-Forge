"""``anomaly_score.v1`` — score deviation from a baseline.

ADR-0033 Phase B2. The other half of AnomalyAce's surface — pair
this with ``behavioral_baseline.v1`` to compute a deviation score
for a fresh window of events against a recalled baseline.

Per-field scoring follows the baseline's type:

  * **categorical** — for each present value, score = 1.0 if the
                      value is novel (not in baseline.frequency),
                      else 1 / (1 + log10(1 + frequency)). Aggregate
                      score for the field is the max per-row score.
                      Operators looking for "did anything new show
                      up?" key on the novel set returned alongside.
  * **numeric**     — z-score against baseline mean+stddev. Aggregate
                      score = max |z| across the window.
  * **timestamp**   — chi-square-ish: project the new events into
                      the baseline's bucket distribution, score how
                      much the proportions diverge. Aggregate score
                      is the resulting statistic, normalized.

Each field's score lands in ``output.fields[field].score`` along
with type-specific evidence (top novel values, max-z row, etc.)
so the operator can see *why* the score is what it is.

A single overall ``score`` is returned alongside (max of per-field
scores) for skills that need a single number to threshold against.

side_effects=read_only — pure function over the events + baseline
the caller passes in.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_EVENTS = 100000
_MAX_FIELDS = 20


class AnomalyScoreTool:
    """Score a window of events against a baseline.

    Args:
      events   (list[dict], required): the new window of events
      baseline (object, required): a baseline as produced by
        behavioral_baseline.v1 (see that tool's output shape)
      fields   (list[str], optional): subset of baseline fields to
        score. Default: every field in the baseline.

    Output:
      {
        "event_count": int,
        "score":       float,    # max of per-field scores
        "fields": {
          "<name>": {
            "type":  str,
            "score": float,
            ... type-specific evidence ...
          }, ...
        }
      }
    """

    name = "anomaly_score"
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
                raise ToolValidationError(
                    f"events[{i}] must be a dict; got {type(e).__name__}"
                )
        baseline = args.get("baseline")
        if not isinstance(baseline, dict):
            raise ToolValidationError(
                "baseline must be the object emitted by behavioral_baseline.v1"
            )
        if "fields" not in baseline or not isinstance(baseline["fields"], dict):
            raise ToolValidationError(
                "baseline.fields must be a mapping (the per-field stats block)"
            )
        sub = args.get("fields")
        if sub is not None:
            if not isinstance(sub, list) or not all(isinstance(s, str) for s in sub):
                raise ToolValidationError(
                    "fields must be a list of strings when provided"
                )
            if len(sub) > _MAX_FIELDS:
                raise ToolValidationError(
                    f"fields must be ≤ {_MAX_FIELDS}; got {len(sub)}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        events: list[dict] = args["events"]
        baseline: dict = args["baseline"]
        baseline_fields: dict = baseline["fields"]
        wanted = args.get("fields") or list(baseline_fields.keys())

        out_fields: dict[str, dict] = {}
        scores: list[float] = []

        for fname in wanted:
            spec = baseline_fields.get(fname)
            if spec is None:
                out_fields[fname] = {
                    "type":  "missing_from_baseline",
                    "score": 0.0,
                    "note":  "field not present in baseline",
                }
                continue
            t = spec.get("type")
            new_values = [e.get(fname) for e in events if e.get(fname) is not None]
            if t == "categorical":
                out_fields[fname] = _score_categorical(spec, new_values)
            elif t == "numeric":
                out_fields[fname] = _score_numeric(spec, new_values)
            elif t == "timestamp":
                out_fields[fname] = _score_timestamp(spec, new_values)
            else:
                out_fields[fname] = {
                    "type":  t or "unknown",
                    "score": 0.0,
                    "note":  f"unsupported baseline type: {t!r}",
                }
            scores.append(float(out_fields[fname].get("score", 0.0)))

        overall = max(scores) if scores else 0.0

        return ToolResult(
            output={
                "event_count": len(events),
                "score":       overall,
                "fields":      out_fields,
            },
            metadata={
                "fields_scored": len(out_fields),
                "max_field":     max(out_fields.items(), key=lambda kv: kv[1].get("score", 0.0))[0]
                                  if out_fields else None,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=f"score={overall:.3f} over {len(events)} events",
        )


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------
def _score_categorical(spec: dict, new_values: list[Any]) -> dict:
    """Per-value novelty score. Score = 1.0 if value never seen in
    baseline; 1 / (1 + log10(1 + count)) otherwise (rare values
    score higher than common ones). Aggregate = max per-row."""
    freq: dict = spec.get("frequency") or {}
    novel: list[str] = []
    rare_scores: list[float] = []
    seen_counts: Counter = Counter()
    for v in new_values:
        s = v if isinstance(v, str) else repr(v)
        seen_counts[s] += 1
        if s not in freq:
            novel.append(s)
        else:
            count = float(freq[s])
            score = 1.0 / (1.0 + math.log10(1.0 + count))
            rare_scores.append(score)
    novel_unique = sorted(set(novel))
    score = 1.0 if novel_unique else (max(rare_scores) if rare_scores else 0.0)
    return {
        "type":         "categorical",
        "score":        score,
        "novel":        novel_unique[:50],   # cap visible list
        "novel_count":  len(novel_unique),
        "novel_total_occurrences": len(novel),
        "rare_max_score": max(rare_scores) if rare_scores else 0.0,
        "values_seen":  len(new_values),
    }


def _score_numeric(spec: dict, new_values: list[Any]) -> dict:
    """Z-score against baseline mean+stddev. When stddev is zero
    (constant baseline), any deviation is ∞-equivalent — we cap at
    100.0 so the operator gets a finite high signal rather than NaN."""
    nums: list[float] = []
    for v in new_values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    if not nums:
        return {
            "type":  "numeric",
            "score": 0.0,
            "note":  "no numeric values in window",
        }
    mean = float(spec.get("mean") or 0.0)
    stddev = float(spec.get("stddev") or 0.0)
    z_scores: list[float] = []
    for x in nums:
        if stddev == 0.0:
            if x == mean:
                z_scores.append(0.0)
            else:
                z_scores.append(100.0)
        else:
            z_scores.append((x - mean) / stddev)
    abs_z = [abs(z) for z in z_scores]
    max_idx = abs_z.index(max(abs_z))
    return {
        "type":         "numeric",
        "score":        abs_z[max_idx],
        "max_z":        z_scores[max_idx],
        "max_z_value":  nums[max_idx],
        "baseline_mean":   mean,
        "baseline_stddev": stddev,
        "values_seen":  len(nums),
    }


def _score_timestamp(spec: dict, new_values: list[Any]) -> dict:
    """Chi-square-ish divergence. Project new events into the
    baseline's bucket distribution and compare proportions."""
    from forest_soul_forge.tools.builtin.behavioral_baseline import _parse_timestamp

    baseline_buckets: list[int] = list(spec.get("buckets") or [])
    if not baseline_buckets or len(baseline_buckets) < 2:
        return {
            "type":  "timestamp",
            "score": 0.0,
            "note":  "baseline has insufficient buckets",
        }
    earliest_str = spec.get("earliest")
    latest_str = spec.get("latest")
    bucket_size = float(spec.get("bucket_size_seconds") or 0.0)
    if not earliest_str or not latest_str or bucket_size <= 0:
        return {
            "type":  "timestamp",
            "score": 0.0,
            "note":  "baseline missing time range or bucket size",
        }
    earliest = _parse_timestamp(earliest_str)
    if earliest is None:
        return {
            "type":  "timestamp",
            "score": 0.0,
            "note":  "baseline.earliest not parseable",
        }

    n_buckets = len(baseline_buckets)
    new_counts = [0] * n_buckets
    out_of_range = 0
    parsed_count = 0
    for v in new_values:
        dt = _parse_timestamp(v)
        if dt is None:
            continue
        offset = (dt - earliest).total_seconds()
        if offset < 0 or offset >= n_buckets * bucket_size:
            out_of_range += 1
            continue
        idx = min(int(offset / bucket_size), n_buckets - 1)
        new_counts[idx] += 1
        parsed_count += 1

    if parsed_count == 0:
        return {
            "type":  "timestamp",
            "score": 0.0,
            "note":  "no timestamps in window mapped to baseline range",
            "out_of_range": out_of_range,
        }

    # Pearson-style chi-square per bucket against the baseline's
    # proportions, normalized by total counts. We're not doing a
    # real chi-square test (no degrees-of-freedom interpretation);
    # we want a single number that grows with divergence.
    base_total = sum(baseline_buckets) or 1
    new_total = sum(new_counts) or 1
    chi = 0.0
    for b, n in zip(baseline_buckets, new_counts):
        expected = (b / base_total) * new_total
        if expected > 0:
            chi += ((n - expected) ** 2) / expected
    # Normalize to keep "score" in a comparable range with
    # categorical (0..1ish) and numeric (z-scores). Divide by
    # n_buckets so the magnitude doesn't blow up with bucket count.
    score = chi / n_buckets
    return {
        "type":         "timestamp",
        "score":        score,
        "chi_squared":  chi,
        "buckets":      new_counts,
        "out_of_range": out_of_range,
        "values_seen":  parsed_count,
    }
