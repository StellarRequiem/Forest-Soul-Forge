"""Tests for ADR-0087 Phase B — calendar_block.v1 builtin tool."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.calendar_block import (
    CalendarBlockTool,
)


def _ctx():
    return ToolContext(
        instance_id="time_steward_test",
        agent_dna="a" * 12,
        role="time_steward",
        genre="actuator",
        session_id=None,
    )


def _run(args):
    return asyncio.run(CalendarBlockTool().execute(args, _ctx()))


def _future_iso(seconds_ahead: int = 3600) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds_ahead)
    ).isoformat(timespec="seconds")


class TestValidation:
    def test_operation_required(self):
        with pytest.raises(ToolValidationError, match="operation"):
            CalendarBlockTool().validate({})

    def test_invalid_operation_rejected(self):
        with pytest.raises(ToolValidationError, match="operation"):
            CalendarBlockTool().validate({"operation": "delete_all"})

    def test_create_requires_start(self):
        with pytest.raises(ToolValidationError, match="start"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "end": _future_iso(7200),
                    "subject": "x",
                }
            )

    def test_create_requires_end(self):
        with pytest.raises(ToolValidationError, match="end"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "start": _future_iso(3600),
                    "subject": "x",
                }
            )

    def test_create_requires_subject(self):
        with pytest.raises(ToolValidationError, match="subject"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "start": _future_iso(3600),
                    "end": _future_iso(7200),
                }
            )

    def test_end_must_be_after_start(self):
        start = _future_iso(7200)
        end = _future_iso(3600)
        with pytest.raises(ToolValidationError, match="after start"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "start": start,
                    "end": end,
                    "subject": "x",
                }
            )

    def test_start_must_be_future(self):
        past = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat(timespec="seconds")
        future = _future_iso(3600)
        with pytest.raises(ToolValidationError, match="future"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "start": past,
                    "end": future,
                    "subject": "x",
                }
            )

    def test_subject_too_long(self):
        with pytest.raises(ToolValidationError, match="subject"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "start": _future_iso(3600),
                    "end": _future_iso(7200),
                    "subject": "x" * 501,
                }
            )

    def test_body_too_long(self):
        with pytest.raises(ToolValidationError, match="body"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "start": _future_iso(3600),
                    "end": _future_iso(7200),
                    "subject": "x",
                    "body": "y" * 5001,
                }
            )

    def test_attendees_must_be_list(self):
        with pytest.raises(ToolValidationError, match="attendees"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "start": _future_iso(3600),
                    "end": _future_iso(7200),
                    "subject": "x",
                    "attendees": "alice@example.com",
                }
            )

    def test_attendees_each_must_be_email_like(self):
        with pytest.raises(ToolValidationError, match="attendee"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "start": _future_iso(3600),
                    "end": _future_iso(7200),
                    "subject": "x",
                    "attendees": ["not-an-email"],
                }
            )

    def test_attendees_count_capped(self):
        with pytest.raises(ToolValidationError, match="attendees"):
            CalendarBlockTool().validate(
                {
                    "operation": "create",
                    "start": _future_iso(3600),
                    "end": _future_iso(7200),
                    "subject": "x",
                    "attendees": [f"a{i}@example.com" for i in range(51)],
                }
            )

    def test_decline_requires_event_id(self):
        with pytest.raises(ToolValidationError, match="event_id"):
            CalendarBlockTool().validate({"operation": "decline"})

    def test_cancel_requires_event_id(self):
        with pytest.raises(ToolValidationError, match="event_id"):
            CalendarBlockTool().validate({"operation": "cancel"})

    def test_valid_create_args_ok(self):
        CalendarBlockTool().validate(
            {
                "operation": "create",
                "start": _future_iso(3600),
                "end": _future_iso(7200),
                "subject": "Project review",
                "body": "agenda",
                "attendees": ["a@example.com"],
            }
        )

    def test_valid_decline_args_ok(self):
        CalendarBlockTool().validate(
            {
                "operation": "decline",
                "event_id": "evt-123",
                "decline_message": "scheduling conflict",
            }
        )

    def test_valid_cancel_args_ok(self):
        CalendarBlockTool().validate(
            {"operation": "cancel", "event_id": "evt-456"}
        )


class TestExecuteConnectorGate:
    def test_refuses_when_connector_absent(self, tmp_path):
        # assume_connector defaults False; no marker file = refuse
        with pytest.raises(ToolValidationError, match="connector"):
            _run(
                {
                    "operation": "create",
                    "start": _future_iso(3600),
                    "end": _future_iso(7200),
                    "subject": "Project review",
                    "queue_path": str(tmp_path / "queue.jsonl"),
                }
            )


class TestExecuteWithConnector:
    def test_create_queues_record(self, tmp_path):
        queue = tmp_path / "queue.jsonl"
        result = _run(
            {
                "operation": "create",
                "start": _future_iso(3600),
                "end": _future_iso(7200),
                "subject": "Project review",
                "body": "agenda",
                "attendees": ["alice@example.com"],
                "queue_path": str(queue),
                "assume_connector": True,
            }
        )
        assert queue.exists()
        rec = json.loads(queue.read_text().splitlines()[0])
        assert rec["operation"] == "create"
        assert rec["payload"]["subject"] == "Project review"
        assert rec["payload"]["attendees"] == ["alice@example.com"]
        assert result.output["connector_present"] is True
        assert result.output["request_id"].startswith("cal_")

    def test_decline_queues_record(self, tmp_path):
        queue = tmp_path / "queue.jsonl"
        _run(
            {
                "operation": "decline",
                "event_id": "evt-789",
                "decline_message": "conflict",
                "queue_path": str(queue),
                "assume_connector": True,
            }
        )
        rec = json.loads(queue.read_text().splitlines()[0])
        assert rec["operation"] == "decline"
        assert rec["payload"]["event_id"] == "evt-789"
        assert rec["payload"]["decline_message"] == "conflict"

    def test_cancel_queues_record(self, tmp_path):
        queue = tmp_path / "queue.jsonl"
        _run(
            {
                "operation": "cancel",
                "event_id": "evt-999",
                "queue_path": str(queue),
                "assume_connector": True,
            }
        )
        rec = json.loads(queue.read_text().splitlines()[0])
        assert rec["operation"] == "cancel"
        assert rec["payload"]["event_id"] == "evt-999"

    def test_appends_multiple_records(self, tmp_path):
        queue = tmp_path / "queue.jsonl"
        _run(
            {
                "operation": "cancel",
                "event_id": "evt-1",
                "queue_path": str(queue),
                "assume_connector": True,
            }
        )
        _run(
            {
                "operation": "cancel",
                "event_id": "evt-2",
                "queue_path": str(queue),
                "assume_connector": True,
            }
        )
        assert len(queue.read_text().splitlines()) == 2

    def test_metadata_carries_request_id(self, tmp_path):
        queue = tmp_path / "queue.jsonl"
        result = _run(
            {
                "operation": "cancel",
                "event_id": "evt-x",
                "queue_path": str(queue),
                "assume_connector": True,
            }
        )
        assert result.metadata["request_id"] == result.output["request_id"]
        assert result.metadata["operation"] == "cancel"

    def test_side_effect_summary_present(self, tmp_path):
        queue = tmp_path / "queue.jsonl"
        result = _run(
            {
                "operation": "cancel",
                "event_id": "evt-x",
                "queue_path": str(queue),
                "assume_connector": True,
            }
        )
        assert result.side_effect_summary is not None
        assert "calendar" in result.side_effect_summary

    def test_attestor_recorded(self, tmp_path):
        queue = tmp_path / "queue.jsonl"
        _run(
            {
                "operation": "cancel",
                "event_id": "evt-x",
                "queue_path": str(queue),
                "assume_connector": True,
            }
        )
        rec = json.loads(queue.read_text().splitlines()[0])
        assert rec["attestor"] == "time_steward_test"
        assert rec["agent_role"] == "time_steward"

    def test_creates_parent_dir(self, tmp_path):
        queue = tmp_path / "nested" / "deep" / "queue.jsonl"
        _run(
            {
                "operation": "cancel",
                "event_id": "evt-x",
                "queue_path": str(queue),
                "assume_connector": True,
            }
        )
        assert queue.exists()


class TestSchema:
    def test_name_version(self):
        t = CalendarBlockTool()
        assert t.name == "calendar_block"
        assert t.version == "1"

    def test_side_effects_external(self):
        assert CalendarBlockTool().side_effects == "external"
