"""Tests for ADR-0091 Phase B — energy_anomaly_scan.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.energy_anomaly_scan import (
    EnergyAnomalyScanTool,
)


def _ctx():
    return ToolContext(
        instance_id="energy_warden_test",
        agent_dna="a" * 12,
        role="energy_warden",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(EnergyAnomalyScanTool().execute(args, _ctx()))


def _r(slug, watts, mean=100.0, stddev=10.0, count=20, **kw):
    base = {
        "device_slug":             slug,
        "current_watts":           watts,
        "baseline_mean_watts":     mean,
        "baseline_stddev_watts":   stddev,
        "baseline_sample_count":   count,
    }
    base.update(kw)
    return base


class TestValidation:
    def test_window_slug_required(self):
        with pytest.raises(ToolValidationError, match="window_slug"):
            EnergyAnomalyScanTool().validate({"readings": [_r("a", 100)]})

    def test_window_slug_must_be_string(self):
        with pytest.raises(ToolValidationError, match="window_slug"):
            EnergyAnomalyScanTool().validate(
                {"window_slug": 1, "readings": [_r("a", 100)]}
            )

    def test_readings_required(self):
        with pytest.raises(ToolValidationError, match="readings"):
            EnergyAnomalyScanTool().validate({"window_slug": "w1"})

    def test_readings_must_be_list(self):
        with pytest.raises(ToolValidationError, match="readings"):
            EnergyAnomalyScanTool().validate(
                {"window_slug": "w1", "readings": "not a list"}
            )

    def test_readings_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="at least one"):
            EnergyAnomalyScanTool().validate(
                {"window_slug": "w1", "readings": []}
            )

    def test_readings_capped(self):
        big = [_r(f"d{i}", 100) for i in range(201)]
        with pytest.raises(ToolValidationError, match="200"):
            EnergyAnomalyScanTool().validate(
                {"window_slug": "w1", "readings": big}
            )

    def test_device_slug_unique(self):
        with pytest.raises(ToolValidationError, match="duplicates"):
            EnergyAnomalyScanTool().validate({
                "window_slug": "w1",
                "readings": [_r("a", 100), _r("a", 200)],
            })

    def test_current_watts_required(self):
        with pytest.raises(ToolValidationError, match="current_watts"):
            EnergyAnomalyScanTool().validate({
                "window_slug": "w1",
                "readings": [{"device_slug": "a"}],
            })

    def test_current_watts_must_be_number(self):
        with pytest.raises(ToolValidationError, match="current_watts"):
            EnergyAnomalyScanTool().validate({
                "window_slug": "w1",
                "readings": [{"device_slug": "a", "current_watts": "lots"}],
            })

    def test_current_watts_non_negative(self):
        with pytest.raises(ToolValidationError, match=">= 0"):
            EnergyAnomalyScanTool().validate({
                "window_slug": "w1",
                "readings": [{"device_slug": "a", "current_watts": -1}],
            })

    def test_spike_sigma_must_exceed_drift_sigma(self):
        with pytest.raises(ToolValidationError, match="strictly greater"):
            EnergyAnomalyScanTool().validate({
                "window_slug": "w1",
                "readings": [_r("a", 100)],
                "spike_sigma": 1.5,
                "drift_sigma": 1.5,
            })

    def test_spike_sigma_must_be_positive(self):
        with pytest.raises(ToolValidationError, match="spike_sigma"):
            EnergyAnomalyScanTool().validate({
                "window_slug": "w1",
                "readings": [_r("a", 100)],
                "spike_sigma": 0,
            })

    def test_min_baseline_samples_floor(self):
        with pytest.raises(ToolValidationError, match="min_baseline_samples"):
            EnergyAnomalyScanTool().validate({
                "window_slug": "w1",
                "readings": [_r("a", 100)],
                "min_baseline_samples": 0,
            })

    def test_baseline_sample_count_non_negative_integer(self):
        with pytest.raises(ToolValidationError, match="baseline_sample_count"):
            EnergyAnomalyScanTool().validate({
                "window_slug": "w1",
                "readings": [_r("a", 100, count=-3)],
            })

    def test_baseline_mean_must_be_non_negative(self):
        with pytest.raises(ToolValidationError, match="baseline_mean_watts"):
            EnergyAnomalyScanTool().validate({
                "window_slug": "w1",
                "readings": [_r("a", 100, mean=-5)],
            })


class TestVerdicts:
    def test_normal_within_drift_sigma(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 105.0)],  # z = 0.5
        })
        v = result.output["verdicts"][0]
        assert v["verdict"] == "normal"
        assert v["z_score"] == 0.5

    def test_drift_between_drift_and_spike(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 120.0)],  # z = 2.0 (between 1.5 and 3)
        })
        assert result.output["verdicts"][0]["verdict"] == "drift"

    def test_spike_at_or_above_spike_sigma(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 135.0)],  # z = 3.5 >= 3
        })
        assert result.output["verdicts"][0]["verdict"] == "spike"

    def test_negative_z_score_classified_by_magnitude(self):
        # Below baseline by 3.5 sigma.
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 65.0)],
        })
        v = result.output["verdicts"][0]
        assert v["verdict"] == "spike"
        assert v["z_score"] == -3.5

    def test_missing_baseline_when_stddev_zero(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 100.0, stddev=0)],
        })
        assert result.output["verdicts"][0]["verdict"] == "missing_baseline"

    def test_missing_baseline_when_sample_count_low(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 100.0, count=3)],  # below default 5
        })
        assert result.output["verdicts"][0]["verdict"] == "missing_baseline"

    def test_missing_baseline_when_mean_missing(self):
        result = _run({
            "window_slug": "w1",
            "readings": [{
                "device_slug": "fridge",
                "current_watts": 100,
            }],
        })
        assert result.output["verdicts"][0]["verdict"] == "missing_baseline"

    def test_custom_thresholds_relax_classification(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 120.0)],  # z=2
            "drift_sigma": 2.5,
            "spike_sigma": 5.0,
        })
        # 2.0 < 2.5 → normal under relaxed thresholds
        assert result.output["verdicts"][0]["verdict"] == "normal"

    def test_summary_counts_match(self):
        result = _run({
            "window_slug": "w1",
            "readings": [
                _r("a", 100.0),                            # normal
                _r("b", 120.0),                            # drift
                _r("c", 140.0),                            # spike
                _r("d", 100.0, stddev=0),                  # missing
            ],
        })
        s = result.output["summary"]
        assert s["device_count"] == 4
        assert s["normal_count"] == 1
        assert s["drift_count"] == 1
        assert s["spike_count"] == 1
        assert s["missing_baseline_count"] == 1

    def test_max_abs_z_score_tracked(self):
        result = _run({
            "window_slug": "w1",
            "readings": [
                _r("a", 105.0),    # z=0.5
                _r("b", 140.0),    # z=4.0
                _r("c", 130.0),    # z=3.0
            ],
        })
        assert result.output["summary"]["max_abs_z_score"] == 4.0

    def test_min_baseline_samples_param_applied(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 100.0, count=20)],
            "min_baseline_samples": 50,  # 20 < 50 → missing_baseline
        })
        assert result.output["verdicts"][0]["verdict"] == "missing_baseline"

    def test_label_defaults_to_device_slug(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 100.0)],
        })
        assert result.output["verdicts"][0]["label"] == "fridge"

    def test_label_preserved_when_provided(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 100.0, label="Kitchen Fridge")],
        })
        assert result.output["verdicts"][0]["label"] == "Kitchen Fridge"

    def test_room_passed_through(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("fridge", 100.0, room="kitchen")],
        })
        assert result.output["verdicts"][0]["room"] == "kitchen"

    def test_window_slug_echoed(self):
        result = _run({
            "window_slug": "evening-2026-05-24",
            "readings": [_r("a", 100.0)],
        })
        assert result.output["window_slug"] == "evening-2026-05-24"


class TestDeterminism:
    def test_same_inputs_produce_same_verdicts(self):
        args = {
            "window_slug": "w1",
            "readings": [
                _r("a", 105.0),
                _r("b", 120.0),
                _r("c", 140.0),
            ],
        }
        r1 = _run(args)
        r2 = _run(args)
        # Drop generated_at (timestamp differs).
        for r in (r1.output, r2.output):
            r.pop("generated_at")
        assert r1.output == r2.output


class TestMetadata:
    def test_side_effect_summary_includes_counts(self):
        result = _run({
            "window_slug": "w1",
            "readings": [
                _r("a", 140.0),    # spike
                _r("b", 120.0),    # drift
                _r("c", 100.0, stddev=0),  # missing
            ],
        })
        assert "1 spike" in result.side_effect_summary
        assert "1 drift" in result.side_effect_summary
        assert "1 missing-baseline" in result.side_effect_summary

    def test_metadata_carries_window_and_counts(self):
        result = _run({
            "window_slug": "w1",
            "readings": [_r("a", 140.0), _r("b", 120.0)],
        })
        assert result.metadata["window_slug"] == "w1"
        assert result.metadata["device_count"] == 2
        assert result.metadata["spike_count"] == 1
        assert result.metadata["drift_count"] == 1
