"""Numerical scoring functions per ADR-0023 T1.

Each function takes a ``scoring_inputs`` dict (built by the fixture
executor) and returns a single float. Functions are deterministic
+ pure — no I/O, no clock reads, no random sampling. The fixture's
``scoring.function`` field names which one runs.

Adding a new function:
  1. Define the callable here.
  2. Add to ``SCORING_FUNCTIONS``.
  3. Add a unit test that pins the contract on representative inputs.

T1 ships five functions covering ADR-0023's named examples:
  detection_rate, false_positive_rate, latency_ms, exact_match,
  composite.

Validation responsibility: each function assumes its required keys
are present in ``scoring_inputs`` and of correct numeric type;
``KeyError`` / ``TypeError`` are caller errors at fixture-execution
time, not function bugs. Callers should be fixture executors that
already shaped the dict to match the scoring contract.
"""
from __future__ import annotations

import statistics
from typing import Any, Callable


def detection_rate(scoring_inputs: dict[str, Any]) -> float:
    """True positives / total labeled positives. Range [0, 1].

    Required keys:
      - true_positives:    int >= 0
      - total_positives:   int > 0  (zero positives = undefined; raise)
    """
    tp = int(scoring_inputs["true_positives"])
    total = int(scoring_inputs["total_positives"])
    if total <= 0:
        raise ValueError(f"detection_rate: total_positives must be > 0; got {total}")
    return max(0.0, min(1.0, tp / total))


def false_positive_rate(scoring_inputs: dict[str, Any]) -> float:
    """False positives / total labeled negatives. Range [0, 1].

    Required keys:
      - false_positives:   int >= 0
      - total_negatives:   int > 0
    """
    fp = int(scoring_inputs["false_positives"])
    total = int(scoring_inputs["total_negatives"])
    if total <= 0:
        raise ValueError(f"false_positive_rate: total_negatives must be > 0; got {total}")
    return max(0.0, min(1.0, fp / total))


def latency_ms(scoring_inputs: dict[str, Any]) -> float:
    """Median latency in milliseconds across a list of timings.

    Required keys:
      - timings_ms:  list of numeric values

    Median, not mean: robust to outliers (a single timing blowup
    during a fixture run shouldn't dominate the score). Operators
    who want mean can register a separate function.
    """
    timings = scoring_inputs["timings_ms"]
    if not isinstance(timings, list) or not timings:
        raise ValueError(f"latency_ms: timings_ms must be a non-empty list; got {timings!r}")
    return float(statistics.median(timings))


def exact_match(scoring_inputs: dict[str, Any]) -> float:
    """1.0 if predicted == expected, else 0.0.

    Required keys:
      - predicted:  any
      - expected:   any (same shape as predicted)
    """
    return 1.0 if scoring_inputs["predicted"] == scoring_inputs["expected"] else 0.0


def composite(scoring_inputs: dict[str, Any]) -> float:
    """Weighted sum of sub-scores. Range depends on weights.

    Required keys:
      - scores:   dict[str, float]    — per-subscore values
      - weights:  dict[str, float]    — same keys as scores; should sum to ~1.0
                                        (not enforced — operator may use unnormalized
                                        weights intentionally)

    Returns sum(scores[k] * weights[k] for k in weights).
    Missing key in scores when present in weights = ValueError.
    """
    scores = scoring_inputs["scores"]
    weights = scoring_inputs["weights"]
    if not isinstance(scores, dict) or not isinstance(weights, dict):
        raise ValueError("composite: scores and weights must both be mappings")
    if not weights:
        raise ValueError("composite: weights must be non-empty")
    total = 0.0
    for key, weight in weights.items():
        if key not in scores:
            raise ValueError(f"composite: weights references {key!r} but scores has no entry")
        total += float(scores[key]) * float(weight)
    return total


SCORING_FUNCTIONS: dict[str, Callable[[dict[str, Any]], float]] = {
    "detection_rate": detection_rate,
    "false_positive_rate": false_positive_rate,
    "latency_ms": latency_ms,
    "exact_match": exact_match,
    "composite": composite,
}
