"""Tests for ADR-0023 T1 numerical scoring functions.

Each scoring function gets a happy-path test + edge-case tests
that pin the contract on representative inputs.
"""
from __future__ import annotations

import pytest

from forest_soul_forge.benchmarks.scoring import (
    SCORING_FUNCTIONS,
    composite,
    detection_rate,
    exact_match,
    false_positive_rate,
    latency_ms,
)


def test_scoring_functions_registry_keys() -> None:
    """T1 ships exactly these five named functions. Lock the surface
    so additions land deliberately with their own tests."""
    assert set(SCORING_FUNCTIONS.keys()) == {
        "detection_rate",
        "false_positive_rate",
        "latency_ms",
        "exact_match",
        "composite",
    }


# ──────────────────────────────────────────────────────────────────────
# detection_rate
# ──────────────────────────────────────────────────────────────────────

def test_detection_rate_happy_path() -> None:
    assert detection_rate({"true_positives": 85, "total_positives": 100}) == pytest.approx(0.85)


def test_detection_rate_perfect_score() -> None:
    assert detection_rate({"true_positives": 100, "total_positives": 100}) == pytest.approx(1.0)


def test_detection_rate_zero_detections() -> None:
    assert detection_rate({"true_positives": 0, "total_positives": 100}) == pytest.approx(0.0)


def test_detection_rate_clamps_at_one() -> None:
    # over-detection (TP > total) shouldn't exceed 1.0 even though
    # logically it's noise; clamp protects downstream consumers.
    assert detection_rate({"true_positives": 150, "total_positives": 100}) == pytest.approx(1.0)


def test_detection_rate_zero_total_raises() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        detection_rate({"true_positives": 0, "total_positives": 0})


# ──────────────────────────────────────────────────────────────────────
# false_positive_rate
# ──────────────────────────────────────────────────────────────────────

def test_false_positive_rate_happy_path() -> None:
    assert false_positive_rate({"false_positives": 5, "total_negatives": 100}) == pytest.approx(0.05)


def test_false_positive_rate_no_false_positives() -> None:
    assert false_positive_rate({"false_positives": 0, "total_negatives": 100}) == pytest.approx(0.0)


def test_false_positive_rate_zero_negatives_raises() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        false_positive_rate({"false_positives": 0, "total_negatives": 0})


# ──────────────────────────────────────────────────────────────────────
# latency_ms
# ──────────────────────────────────────────────────────────────────────

def test_latency_ms_median_odd_count() -> None:
    assert latency_ms({"timings_ms": [10, 20, 30, 40, 50]}) == pytest.approx(30.0)


def test_latency_ms_median_even_count() -> None:
    # median of even-count list = mean of middle two
    assert latency_ms({"timings_ms": [10, 20, 30, 40]}) == pytest.approx(25.0)


def test_latency_ms_single_value() -> None:
    assert latency_ms({"timings_ms": [42]}) == pytest.approx(42.0)


def test_latency_ms_robust_to_outliers() -> None:
    """Median's value-add: a single huge outlier doesn't dominate.
    Mean of [10,10,10,10,10000] would be ~2008; median is 10.
    The fixture's pass threshold is meaningful against median."""
    assert latency_ms({"timings_ms": [10, 10, 10, 10, 10000]}) == pytest.approx(10.0)


def test_latency_ms_empty_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        latency_ms({"timings_ms": []})


def test_latency_ms_non_list_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        latency_ms({"timings_ms": "not a list"})


# ──────────────────────────────────────────────────────────────────────
# exact_match
# ──────────────────────────────────────────────────────────────────────

def test_exact_match_string_equal() -> None:
    assert exact_match({"predicted": "hello", "expected": "hello"}) == pytest.approx(1.0)


def test_exact_match_string_unequal() -> None:
    assert exact_match({"predicted": "hello", "expected": "world"}) == pytest.approx(0.0)


def test_exact_match_works_on_arbitrary_shapes() -> None:
    """Lists, dicts, nested structures: as long as `==` agrees,
    the function returns 1.0."""
    assert exact_match({"predicted": [1, 2, 3], "expected": [1, 2, 3]}) == pytest.approx(1.0)
    assert exact_match({"predicted": {"k": "v"}, "expected": {"k": "v"}}) == pytest.approx(1.0)
    assert exact_match({"predicted": [1, 2, 3], "expected": [1, 2, 4]}) == pytest.approx(0.0)


# ──────────────────────────────────────────────────────────────────────
# composite
# ──────────────────────────────────────────────────────────────────────

def test_composite_weighted_sum() -> None:
    result = composite({
        "scores": {"recall": 0.8, "precision": 0.6, "f1": 0.7},
        "weights": {"recall": 0.5, "precision": 0.3, "f1": 0.2},
    })
    # 0.8*0.5 + 0.6*0.3 + 0.7*0.2 = 0.4 + 0.18 + 0.14 = 0.72
    assert result == pytest.approx(0.72)


def test_composite_unnormalized_weights() -> None:
    """Operator may use unnormalized weights intentionally (e.g.,
    to emphasize one sub-score). The function doesn't enforce
    weights.sum() == 1 — just multiplies + adds."""
    result = composite({
        "scores": {"a": 0.5},
        "weights": {"a": 4.0},
    })
    assert result == pytest.approx(2.0)


def test_composite_missing_score_key_raises() -> None:
    with pytest.raises(ValueError, match="weights references"):
        composite({
            "scores": {"a": 0.5},
            "weights": {"a": 0.5, "b": 0.5},
        })


def test_composite_empty_weights_raises() -> None:
    with pytest.raises(ValueError, match="weights must be non-empty"):
        composite({"scores": {}, "weights": {}})
