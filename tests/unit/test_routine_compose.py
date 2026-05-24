"""Tests for ADR-0091 Phase C — routine_compose.v1 builtin tool."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.routine_compose import RoutineComposeTool


def _ctx():
    return ToolContext(
        instance_id="routine_composer_test",
        agent_dna="a" * 12,
        role="routine_composer",
        genre="actuator",
        session_id="sess-1",
    )


def _run(args):
    return asyncio.run(RoutineComposeTool().execute(args, _ctx()))


def _base_args(tmp_path, **overrides):
    args = {
        "routine_kind": "morning_sequence",
        "name": "weekday morning",
        "scheduled_for": "2026-05-25T07:00:00-07:00",
        "actions": [
            {"device_id": "kitchen_lights", "command": "on", "args": {"pct": 80}},
            {"device_id": "coffee_maker", "command": "on"},
        ],
        "queue_path": str(tmp_path / "queue.jsonl"),
    }
    args.update(overrides)
    return args


class TestValidation:
    def test_routine_kind_required(self):
        with pytest.raises(ToolValidationError, match="routine_kind"):
            RoutineComposeTool().validate({})

    def test_routine_kind_must_be_known(self):
        with pytest.raises(ToolValidationError, match="routine_kind"):
            RoutineComposeTool().validate({"routine_kind": "explode"})

    def test_name_required(self):
        with pytest.raises(ToolValidationError, match="name"):
            RoutineComposeTool().validate({"routine_kind": "vacation_mode"})

    def test_name_too_long(self):
        with pytest.raises(ToolValidationError, match="name"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "x" * 201,
            })

    def test_scheduled_for_required(self):
        with pytest.raises(ToolValidationError, match="scheduled_for"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
            })

    def test_scheduled_for_must_parse(self):
        with pytest.raises(ToolValidationError, match="scheduled_for"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
                "scheduled_for": "not-iso",
                "actions": [{"device_id": "d", "command": "on"}],
            })

    def test_actions_required(self):
        with pytest.raises(ToolValidationError, match="actions"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
                "scheduled_for": "2026-05-25T07:00:00Z",
            })

    def test_actions_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="non-empty"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
                "scheduled_for": "2026-05-25T07:00:00Z",
                "actions": [],
            })

    def test_actions_capped(self):
        big = [{"device_id": f"d{i}", "command": "on"} for i in range(101)]
        with pytest.raises(ToolValidationError, match="100"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
                "scheduled_for": "2026-05-25T07:00:00Z",
                "actions": big,
            })

    def test_action_device_id_required(self):
        with pytest.raises(ToolValidationError, match="device_id"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
                "scheduled_for": "2026-05-25T07:00:00Z",
                "actions": [{"command": "on"}],
            })

    def test_action_command_required(self):
        with pytest.raises(ToolValidationError, match="command"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
                "scheduled_for": "2026-05-25T07:00:00Z",
                "actions": [{"device_id": "d"}],
            })

    def test_action_args_must_be_object(self):
        with pytest.raises(ToolValidationError, match="args"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
                "scheduled_for": "2026-05-25T07:00:00Z",
                "actions": [{"device_id": "d", "command": "on", "args": "bad"}],
            })

    def test_fire_window_bounds(self):
        with pytest.raises(ToolValidationError, match="fire_window_minutes"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
                "scheduled_for": "2026-05-25T07:00:00Z",
                "actions": [{"device_id": "d", "command": "on"}],
                "fire_window_minutes": 99999,
            })

    def test_operator_reason_too_long(self):
        with pytest.raises(ToolValidationError, match="operator_reason"):
            RoutineComposeTool().validate({
                "routine_kind": "vacation_mode",
                "name": "n",
                "scheduled_for": "2026-05-25T07:00:00Z",
                "actions": [{"device_id": "d", "command": "on"}],
                "operator_reason": "x" * 2001,
            })


class TestExecution:
    def test_basic_queue_write(self, tmp_path):
        args = _base_args(tmp_path)
        out = _run(args)
        body = out.output
        assert body["routine_kind"] == "morning_sequence"
        assert body["name"] == "weekday morning"
        assert body["action_count"] == 2
        assert body["routine_id"].startswith("rt_")
        qf = Path(args["queue_path"])
        assert qf.exists()
        line = qf.read_text().strip()
        rec = json.loads(line)
        assert rec["routine_id"] == body["routine_id"]
        assert rec["attestor"] == "routine_composer_test"
        assert rec["agent_role"] == "routine_composer"

    def test_deterministic_routine_id_same_inputs(self, tmp_path):
        args = _base_args(tmp_path)
        out1 = _run(args)
        args2 = _base_args(tmp_path, queue_path=str(tmp_path / "q2.jsonl"))
        out2 = _run(args2)
        assert out1.output["routine_id"] == out2.output["routine_id"]

    def test_different_actions_yield_different_ids(self, tmp_path):
        args1 = _base_args(tmp_path)
        out1 = _run(args1)
        args2 = _base_args(
            tmp_path,
            queue_path=str(tmp_path / "q2.jsonl"),
            actions=[{"device_id": "alarm", "command": "off"}],
        )
        out2 = _run(args2)
        assert out1.output["routine_id"] != out2.output["routine_id"]

    def test_queue_append_only(self, tmp_path):
        args = _base_args(tmp_path)
        _run(args)
        args2 = _base_args(
            tmp_path,
            name="evening",
            scheduled_for="2026-05-25T22:00:00-07:00",
            actions=[{"device_id": "porch_light", "command": "off"}],
        )
        _run(args2)
        lines = Path(args["queue_path"]).read_text().strip().split("\n")
        assert len(lines) == 2

    def test_vacation_mode_kind(self, tmp_path):
        args = _base_args(tmp_path, routine_kind="vacation_mode",
                          name="memorial day weekend")
        out = _run(args)
        assert out.output["routine_kind"] == "vacation_mode"

    def test_custom_kind(self, tmp_path):
        args = _base_args(tmp_path, routine_kind="custom",
                          name="ad-hoc")
        out = _run(args)
        assert out.output["routine_kind"] == "custom"

    def test_source_snapshot_id_threaded(self, tmp_path):
        args = _base_args(tmp_path, source_snapshot_id="snap-xyz")
        _run(args)
        rec = json.loads(Path(args["queue_path"]).read_text().strip())
        assert rec["source_snapshot_id"] == "snap-xyz"

    def test_operator_reason_threaded(self, tmp_path):
        args = _base_args(tmp_path, operator_reason="weekday morning kickoff")
        _run(args)
        rec = json.loads(Path(args["queue_path"]).read_text().strip())
        assert rec["operator_reason"] == "weekday morning kickoff"

    def test_default_scope_is_all(self, tmp_path):
        args = _base_args(tmp_path)
        _run(args)
        rec = json.loads(Path(args["queue_path"]).read_text().strip())
        assert rec["scope"] == "all"

    def test_custom_scope_threaded(self, tmp_path):
        args = _base_args(tmp_path, scope="kitchen,living_room")
        _run(args)
        rec = json.loads(Path(args["queue_path"]).read_text().strip())
        assert rec["scope"] == "kitchen,living_room"

    def test_fire_window_default(self, tmp_path):
        args = _base_args(tmp_path)
        _run(args)
        rec = json.loads(Path(args["queue_path"]).read_text().strip())
        assert rec["fire_window_min"] == 10

    def test_fire_window_override(self, tmp_path):
        args = _base_args(tmp_path, fire_window_minutes=30)
        _run(args)
        rec = json.loads(Path(args["queue_path"]).read_text().strip())
        assert rec["fire_window_min"] == 30

    def test_zoned_iso_preserved(self, tmp_path):
        args = _base_args(tmp_path)
        out = _run(args)
        # original is Pacific (-07:00)
        assert "-07:00" in out.output["scheduled_for"]

    def test_z_suffix_normalized(self, tmp_path):
        args = _base_args(tmp_path, scheduled_for="2026-05-25T07:00:00Z")
        out = _run(args)
        # tool normalizes Z to +00:00
        assert "+00:00" in out.output["scheduled_for"]

    def test_queue_parent_dir_created(self, tmp_path):
        nested = tmp_path / "nested" / "deeper"
        args = _base_args(tmp_path, queue_path=str(nested / "q.jsonl"))
        _run(args)
        assert (nested / "q.jsonl").exists()

    def test_metadata_summary(self, tmp_path):
        args = _base_args(tmp_path)
        out = _run(args)
        assert out.metadata["routine_kind"] == "morning_sequence"
        assert out.metadata["action_count"] == 2
        assert out.metadata["routine_id"].startswith("rt_")

    def test_side_effect_summary_includes_routine_id(self, tmp_path):
        args = _base_args(tmp_path)
        out = _run(args)
        assert "rt_" in out.side_effect_summary
        assert "morning_sequence" in out.side_effect_summary

    def test_action_order_invariant_for_id(self, tmp_path):
        # ID derivation sorts actions internally so the operator can
        # supply them in any order without breaking idempotency
        args1 = _base_args(tmp_path, actions=[
            {"device_id": "a", "command": "on"},
            {"device_id": "b", "command": "off"},
        ])
        out1 = _run(args1)
        args2 = _base_args(tmp_path, queue_path=str(tmp_path / "q2.jsonl"),
                           actions=[
                               {"device_id": "b", "command": "off"},
                               {"device_id": "a", "command": "on"},
                           ])
        out2 = _run(args2)
        assert out1.output["routine_id"] == out2.output["routine_id"]

    def test_queued_at_is_recorded(self, tmp_path):
        args = _base_args(tmp_path)
        out = _run(args)
        assert "queued_at" in out.output
        assert "T" in out.output["queued_at"]

    def test_all_kinds_accepted(self, tmp_path):
        for kind in ("vacation_mode", "morning_sequence",
                     "focus_mode", "sleep_mode", "custom"):
            args = _base_args(
                tmp_path,
                routine_kind=kind,
                queue_path=str(tmp_path / f"q-{kind}.jsonl"),
            )
            out = _run(args)
            assert out.output["routine_kind"] == kind
