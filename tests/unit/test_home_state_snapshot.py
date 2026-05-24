"""Tests for ADR-0091 Phase C — home_state_snapshot.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.home_state_snapshot import (
    HomeStateSnapshotTool,
)


def _ctx():
    return ToolContext(
        instance_id="routine_composer_test",
        agent_dna="a" * 12,
        role="routine_composer",
        genre="actuator",
        session_id=None,
    )


def _run(args):
    return asyncio.run(HomeStateSnapshotTool().execute(args, _ctx()))


def _rec(did, room, state, observed_at="2026-05-24T18:00:00Z", **kw):
    base = {
        "device_id":   did,
        "room":        room,
        "state":       state,
        "observed_at": observed_at,
    }
    base.update(kw)
    return base


class TestValidation:
    def test_records_required(self):
        with pytest.raises(ToolValidationError, match="records"):
            HomeStateSnapshotTool().validate({})

    def test_records_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="non-empty"):
            HomeStateSnapshotTool().validate({"records": []})

    def test_records_capped(self):
        big = [_rec(f"d{i}", "room", "on") for i in range(501)]
        with pytest.raises(ToolValidationError, match="500"):
            HomeStateSnapshotTool().validate({"records": big})

    def test_each_record_must_be_object(self):
        with pytest.raises(ToolValidationError, match="must be an object"):
            HomeStateSnapshotTool().validate({"records": ["bad"]})

    def test_device_id_required(self):
        with pytest.raises(ToolValidationError, match="device_id"):
            HomeStateSnapshotTool().validate({
                "records": [{"room": "r", "state": "on", "observed_at": "2026-05-24T18:00:00Z"}]
            })

    def test_room_required(self):
        with pytest.raises(ToolValidationError, match="room"):
            HomeStateSnapshotTool().validate({
                "records": [{"device_id": "d", "state": "on", "observed_at": "2026-05-24T18:00:00Z"}]
            })

    def test_state_required(self):
        with pytest.raises(ToolValidationError, match="state"):
            HomeStateSnapshotTool().validate({
                "records": [{"device_id": "d", "room": "r", "observed_at": "2026-05-24T18:00:00Z"}]
            })

    def test_observed_at_required(self):
        with pytest.raises(ToolValidationError, match="observed_at"):
            HomeStateSnapshotTool().validate({
                "records": [{"device_id": "d", "room": "r", "state": "on"}]
            })

    def test_observed_at_parseable(self):
        with pytest.raises(ToolValidationError, match="observed_at"):
            HomeStateSnapshotTool().validate({
                "records": [_rec("d", "r", "on", observed_at="not-an-iso")]
            })

    def test_value_must_be_number(self):
        with pytest.raises(ToolValidationError, match="value"):
            HomeStateSnapshotTool().validate({
                "records": [_rec("d", "r", "on", value="hot")]
            })

    def test_stale_window_minutes_bounds(self):
        with pytest.raises(ToolValidationError, match="stale_window_minutes"):
            HomeStateSnapshotTool().validate({
                "records": [_rec("d", "r", "on")],
                "stale_window_minutes": 0,
            })

    def test_reference_time_parseable(self):
        with pytest.raises(ToolValidationError, match="reference_time"):
            HomeStateSnapshotTool().validate({
                "records": [_rec("d", "r", "on")],
                "reference_time": "garbage",
            })

    def test_device_id_too_long(self):
        with pytest.raises(ToolValidationError, match="device_id"):
            HomeStateSnapshotTool().validate({
                "records": [_rec("x" * 201, "r", "on")]
            })


class TestExecution:
    def test_simple_two_room_snapshot(self):
        recs = [
            _rec("d1", "kitchen", "on", device_kind="light"),
            _rec("d2", "kitchen", "off", device_kind="light"),
            _rec("d3", "bedroom", "off", device_kind="light"),
        ]
        out = _run({"records": recs, "window_slug": "test-w"})
        body = out.output
        assert body["window_slug"] == "test-w"
        assert body["device_count"] == 3
        assert body["room_count"] == 2
        assert body["stale_count"] == 0
        rooms = {r["room"]: r for r in body["rooms"]}
        assert "kitchen" in rooms and "bedroom" in rooms
        assert rooms["kitchen"]["active_devices"] == ["d1"]
        assert rooms["kitchen"]["inactive_devices"] == ["d2"]
        assert rooms["bedroom"]["inactive_devices"] == ["d3"]

    def test_stale_records_flagged(self):
        # observed_at well in the past relative to reference_time
        old = "2025-01-01T00:00:00Z"
        recent = "2026-05-24T18:00:00Z"
        recs = [
            _rec("d_old", "kitchen", "on", observed_at=old),
            _rec("d_new", "kitchen", "on", observed_at=recent),
        ]
        out = _run({
            "records": recs,
            "reference_time": recent,
            "stale_window_minutes": 60,
        })
        body = out.output
        assert body["stale_count"] == 1
        rooms = {r["room"]: r for r in body["rooms"]}
        assert rooms["kitchen"]["stale_devices"] == ["d_old"]

    def test_presence_kind_flips_flag_to_present(self):
        recs = [
            _rec("p1", "front_door", "present", device_kind="presence"),
            _rec("l1", "kitchen", "off", device_kind="light"),
        ]
        out = _run({"records": recs})
        assert out.output["presence_flag"] == "present"

    def test_presence_kind_flips_flag_to_absent(self):
        recs = [
            _rec("p1", "front_door", "away", device_kind="presence"),
            _rec("l1", "kitchen", "off", device_kind="light"),
        ]
        out = _run({"records": recs})
        assert out.output["presence_flag"] == "absent"

    def test_no_presence_kind_yields_unknown(self):
        recs = [
            _rec("l1", "kitchen", "on", device_kind="light"),
        ]
        out = _run({"records": recs})
        assert out.output["presence_flag"] == "unknown"

    def test_thermostat_readings_captured(self):
        recs = [
            _rec("t1", "living", "22C", device_kind="thermostat", value=22.0),
            _rec("t2", "bedroom", "20C", device_kind="thermostat", value=20.0),
        ]
        out = _run({"records": recs})
        rooms = {r["room"]: r for r in out.output["rooms"]}
        assert rooms["living"]["thermostat_readings"][0]["value"] == 22.0
        assert rooms["living"]["thermostat_readings"][0]["unit"] == "C"

    def test_room_rollup_kind_counts(self):
        recs = [
            _rec("l1", "kitchen", "on", device_kind="light"),
            _rec("l2", "kitchen", "off", device_kind="light"),
            _rec("k1", "kitchen", "off", device_kind="lock"),
        ]
        out = _run({"records": recs})
        rooms = {r["room"]: r for r in out.output["rooms"]}
        assert rooms["kitchen"]["device_kinds"] == {"light": 2, "lock": 1}

    def test_metadata_summary(self):
        recs = [_rec("d1", "r1", "on")]
        out = _run({"records": recs, "window_slug": "sw"})
        assert out.metadata["window_slug"] == "sw"
        assert out.metadata["device_count"] == 1
        assert out.metadata["room_count"] == 1

    def test_deterministic_room_ordering(self):
        recs = [
            _rec("a", "zebra", "on"),
            _rec("b", "apple", "on"),
            _rec("c", "mango", "on"),
        ]
        out = _run({"records": recs})
        rooms = [r["room"] for r in out.output["rooms"]]
        assert rooms == ["apple", "mango", "zebra"]

    def test_anomaly_hint_for_stale(self):
        old = "2025-01-01T00:00:00Z"
        recent = "2026-05-24T18:00:00Z"
        recs = [_rec("d_old", "kitchen", "on", observed_at=old)]
        out = _run({
            "records": recs,
            "reference_time": recent,
            "stale_window_minutes": 60,
        })
        assert any("stale" in h for h in out.output["anomaly_hints"])

    def test_zone_offset_observed_at(self):
        recs = [_rec("d1", "r1", "on", observed_at="2026-05-24T18:00:00-07:00")]
        out = _run({"records": recs})
        assert out.output["device_count"] == 1

    def test_side_effects_summary_includes_counts(self):
        recs = [_rec("d1", "r1", "on")]
        out = _run({"records": recs, "window_slug": "ws"})
        assert "1 devices" in out.side_effect_summary

    def test_inactive_state_classified(self):
        recs = [_rec("l1", "k", "off", device_kind="light")]
        out = _run({"records": recs})
        rooms = {r["room"]: r for r in out.output["rooms"]}
        assert "l1" in rooms["k"]["inactive_devices"]
        assert "l1" not in rooms["k"]["active_devices"]
