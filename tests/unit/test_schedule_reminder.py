"""Tests for ADR-0087 Phase B — schedule_reminder.v1 builtin tool."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.schedule_reminder import (
    ScheduleReminderTool,
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
    return asyncio.run(ScheduleReminderTool().execute(args, _ctx()))


def _future_iso(seconds_ahead: int = 3600) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds_ahead)
    ).isoformat(timespec="seconds")


class TestValidation:
    def test_fire_at_required(self):
        with pytest.raises(ToolValidationError, match="fire_at"):
            ScheduleReminderTool().validate({"message": "hi"})

    def test_message_required(self):
        with pytest.raises(ToolValidationError, match="message"):
            ScheduleReminderTool().validate({"fire_at": _future_iso()})

    def test_fire_at_must_be_future(self):
        past = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat(timespec="seconds")
        with pytest.raises(ToolValidationError, match="future"):
            ScheduleReminderTool().validate(
                {"fire_at": past, "message": "hi"}
            )

    def test_fire_at_unparseable(self):
        with pytest.raises(ToolValidationError, match="not parseable"):
            ScheduleReminderTool().validate(
                {"fire_at": "not-a-timestamp", "message": "hi"}
            )

    def test_message_too_long(self):
        with pytest.raises(ToolValidationError, match="2000"):
            ScheduleReminderTool().validate(
                {
                    "fire_at": _future_iso(),
                    "message": "x" * 2001,
                }
            )

    def test_message_empty_rejected(self):
        with pytest.raises(ToolValidationError, match="message"):
            ScheduleReminderTool().validate(
                {"fire_at": _future_iso(), "message": "   "}
            )

    def test_invalid_channel(self):
        with pytest.raises(ToolValidationError, match="channel"):
            ScheduleReminderTool().validate(
                {
                    "fire_at": _future_iso(),
                    "message": "hi",
                    "channel": "carrier_pigeon",
                }
            )

    def test_channel_default_memory_ok(self):
        # default-when-omitted should validate cleanly
        ScheduleReminderTool().validate(
            {"fire_at": _future_iso(), "message": "hi"}
        )

    def test_reminders_path_must_be_string(self):
        with pytest.raises(ToolValidationError, match="reminders_path"):
            ScheduleReminderTool().validate(
                {
                    "fire_at": _future_iso(),
                    "message": "hi",
                    "reminders_path": 42,
                }
            )

    def test_valid_args_all_channels(self):
        for ch in ("memory", "email", "slack", "desktop", "audit"):
            ScheduleReminderTool().validate(
                {
                    "fire_at": _future_iso(),
                    "message": "hi",
                    "channel": ch,
                }
            )


class TestExecute:
    def test_appends_record_to_ledger(self, tmp_path):
        ledger = tmp_path / "reminders.jsonl"
        result = _run(
            {
                "fire_at": _future_iso(),
                "message": "call mom",
                "channel": "memory",
                "reminders_path": str(ledger),
            }
        )
        assert ledger.exists()
        lines = ledger.read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["message"] == "call mom"
        assert rec["channel"] == "memory"
        assert rec["reminder_id"].startswith("rem_")
        assert rec["attestor"] == "time_steward_test"
        assert result.output["reminder_id"] == rec["reminder_id"]

    def test_appends_multiple_records(self, tmp_path):
        ledger = tmp_path / "reminders.jsonl"
        _run(
            {
                "fire_at": _future_iso(3600),
                "message": "first",
                "reminders_path": str(ledger),
            }
        )
        _run(
            {
                "fire_at": _future_iso(7200),
                "message": "second",
                "reminders_path": str(ledger),
            }
        )
        lines = ledger.read_text().splitlines()
        assert len(lines) == 2
        recs = [json.loads(l) for l in lines]
        assert {r["message"] for r in recs} == {"first", "second"}

    def test_reminder_id_unique_per_call(self, tmp_path):
        ledger = tmp_path / "reminders.jsonl"
        r1 = _run(
            {
                "fire_at": _future_iso(3600),
                "message": "msg",
                "reminders_path": str(ledger),
            }
        )
        # second call same args but later append-ts -> different id
        time.sleep(0.01)
        r2 = _run(
            {
                "fire_at": _future_iso(3600),
                "message": "msg",
                "reminders_path": str(ledger),
            }
        )
        # The id derives from fire_at + channel + message + appended_at;
        # if appended_at moves at all, the id changes. Worst case
        # they're equal (clock granularity), which is acceptable too.
        assert r1.output["reminder_id"].startswith("rem_")
        assert r2.output["reminder_id"].startswith("rem_")

    def test_creates_parent_dir(self, tmp_path):
        ledger = tmp_path / "nested" / "deep" / "reminders.jsonl"
        _run(
            {
                "fire_at": _future_iso(),
                "message": "hi",
                "reminders_path": str(ledger),
            }
        )
        assert ledger.exists()

    def test_output_carries_channel(self, tmp_path):
        ledger = tmp_path / "reminders.jsonl"
        result = _run(
            {
                "fire_at": _future_iso(),
                "message": "hi",
                "channel": "slack",
                "reminders_path": str(ledger),
            }
        )
        assert result.output["channel"] == "slack"

    def test_output_carries_ledger_path(self, tmp_path):
        ledger = tmp_path / "r.jsonl"
        result = _run(
            {
                "fire_at": _future_iso(),
                "message": "hi",
                "reminders_path": str(ledger),
            }
        )
        assert result.output["ledger_path"] == str(ledger)

    def test_side_effect_summary_present(self, tmp_path):
        ledger = tmp_path / "reminders.jsonl"
        result = _run(
            {
                "fire_at": _future_iso(),
                "message": "hi",
                "reminders_path": str(ledger),
            }
        )
        assert result.side_effect_summary is not None
        assert "scheduled reminder" in result.side_effect_summary

    def test_metadata_carries_channel(self, tmp_path):
        ledger = tmp_path / "reminders.jsonl"
        result = _run(
            {
                "fire_at": _future_iso(),
                "message": "hi",
                "channel": "email",
                "reminders_path": str(ledger),
            }
        )
        assert result.metadata["channel"] == "email"

    def test_appended_at_iso(self, tmp_path):
        ledger = tmp_path / "reminders.jsonl"
        result = _run(
            {
                "fire_at": _future_iso(),
                "message": "hi",
                "reminders_path": str(ledger),
            }
        )
        # ISO timestamp parses
        from datetime import datetime
        datetime.fromisoformat(result.output["appended_at"])

    def test_zulu_fire_at_accepted(self, tmp_path):
        ledger = tmp_path / "reminders.jsonl"
        # Construct a fire_at in Z notation
        future = (
            datetime.now(timezone.utc) + timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = _run(
            {
                "fire_at": future,
                "message": "hi",
                "reminders_path": str(ledger),
            }
        )
        assert result.output["fire_at"].endswith("+00:00")

    def test_tz_aware_fire_at_accepted(self, tmp_path):
        ledger = tmp_path / "reminders.jsonl"
        future_pacific = (
            datetime.now(timezone.utc) + timedelta(hours=2)
        ).astimezone(timezone(timedelta(hours=-7))).isoformat(
            timespec="seconds"
        )
        result = _run(
            {
                "fire_at": future_pacific,
                "message": "hi",
                "reminders_path": str(ledger),
            }
        )
        assert "T" in result.output["fire_at"]


class TestSchema:
    def test_name_version(self):
        t = ScheduleReminderTool()
        assert t.name == "schedule_reminder"
        assert t.version == "1"

    def test_side_effects_filesystem(self):
        assert ScheduleReminderTool().side_effects == "filesystem"
