"""Tests for ADR-0088 Phase D — publish_schedule.v1 builtin tool."""
from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.publish_schedule import (
    PublishScheduleTool,
)


def _ctx():
    return ToolContext(
        instance_id="distribution_pilot_test",
        agent_dna="a" * 12,
        role="distribution_pilot",
        genre="actuator",
        session_id=None,
    )


def _run(args):
    return asyncio.run(PublishScheduleTool().execute(args, _ctx()))


def _future(minutes: int = 60) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=minutes)
    ).isoformat(timespec="seconds")


def _good_args(**overrides):
    args = {
        "platform": "twitter",
        "title": "Test post",
        "body": "Test body content for the post.",
        "fire_at": _future(60),
    }
    args.update(overrides)
    return args


class TestValidation:
    def test_platform_required(self):
        with pytest.raises(ToolValidationError, match="platform"):
            PublishScheduleTool().validate(
                {"title": "t", "body": "b", "fire_at": _future()},
            )

    def test_platform_must_be_valid(self):
        with pytest.raises(ToolValidationError, match="platform"):
            PublishScheduleTool().validate(_good_args(platform="myspace"))

    def test_title_required(self):
        with pytest.raises(ToolValidationError, match="title"):
            args = _good_args()
            args.pop("title")
            PublishScheduleTool().validate(args)

    def test_title_must_be_nonempty(self):
        with pytest.raises(ToolValidationError, match="title"):
            PublishScheduleTool().validate(_good_args(title="   "))

    def test_title_too_long(self):
        with pytest.raises(ToolValidationError, match="500"):
            PublishScheduleTool().validate(_good_args(title="x" * 501))

    def test_body_required(self):
        with pytest.raises(ToolValidationError, match="body"):
            args = _good_args()
            args.pop("body")
            PublishScheduleTool().validate(args)

    def test_body_must_be_nonempty(self):
        with pytest.raises(ToolValidationError, match="body"):
            PublishScheduleTool().validate(_good_args(body="   "))

    def test_body_too_long(self):
        with pytest.raises(ToolValidationError, match="100000"):
            PublishScheduleTool().validate(_good_args(body="x" * 100_001))

    def test_fire_at_required(self):
        with pytest.raises(ToolValidationError, match="fire_at"):
            args = _good_args()
            args.pop("fire_at")
            PublishScheduleTool().validate(args)

    def test_fire_at_must_be_iso(self):
        with pytest.raises(ToolValidationError, match="fire_at"):
            PublishScheduleTool().validate(_good_args(fire_at="not iso"))

    def test_fire_at_must_be_future(self):
        past = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat(timespec="seconds")
        with pytest.raises(ToolValidationError, match="future"):
            PublishScheduleTool().validate(_good_args(fire_at=past))

    def test_tags_must_be_list(self):
        with pytest.raises(ToolValidationError, match="tags"):
            PublishScheduleTool().validate(_good_args(tags="not a list"))

    def test_tags_count_capped(self):
        with pytest.raises(ToolValidationError, match="20"):
            PublishScheduleTool().validate(
                _good_args(tags=[f"t{i}" for i in range(21)]),
            )

    def test_tag_must_be_string(self):
        with pytest.raises(ToolValidationError, match="tags"):
            PublishScheduleTool().validate(_good_args(tags=[1, 2, 3]))

    def test_tag_too_long(self):
        with pytest.raises(ToolValidationError, match="80"):
            PublishScheduleTool().validate(
                _good_args(tags=["x" * 81]),
            )

    def test_reply_to_must_be_string(self):
        with pytest.raises(ToolValidationError, match="reply_to"):
            PublishScheduleTool().validate(_good_args(reply_to=42))

    def test_queue_path_must_be_string(self):
        with pytest.raises(ToolValidationError, match="queue_path"):
            PublishScheduleTool().validate(_good_args(queue_path=42))

    def test_valid_args_accepted(self):
        PublishScheduleTool().validate(_good_args())


class TestExecute:
    def test_returns_request_id(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            result = _run(_good_args(queue_path=str(qp)))
            assert result.output["request_id"].startswith("pub_")

    def test_queue_record_written(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            _run(_good_args(queue_path=str(qp)))
            assert qp.exists()
            line = qp.read_text().strip().splitlines()[-1]
            record = json.loads(line)
            assert record["platform"] == "twitter"
            assert record["status"] == "pending"
            assert "request_id" in record

    def test_appends_subsequent_records(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            _run(_good_args(queue_path=str(qp), platform="twitter"))
            _run(_good_args(queue_path=str(qp), platform="linkedin"))
            lines = qp.read_text().strip().splitlines()
            assert len(lines) == 2
            records = [json.loads(ln) for ln in lines]
            assert records[0]["platform"] == "twitter"
            assert records[1]["platform"] == "linkedin"

    def test_attestor_captured_from_ctx(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            _run(_good_args(queue_path=str(qp)))
            record = json.loads(qp.read_text().strip())
            assert record["attestor"] == "distribution_pilot_test"
            assert record["agent_role"] == "distribution_pilot"

    def test_tags_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            _run(_good_args(queue_path=str(qp), tags=["governance", "ai"]))
            record = json.loads(qp.read_text().strip())
            assert record["tags"] == ["governance", "ai"]

    def test_metadata_includes_request_id_platform_fire_at(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            result = _run(_good_args(queue_path=str(qp)))
            assert "request_id" in result.metadata
            assert result.metadata["platform"] == "twitter"
            assert "fire_at" in result.metadata

    def test_side_effect_summary_present(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            result = _run(_good_args(queue_path=str(qp)))
            assert "queued" in result.side_effect_summary
            assert "twitter" in result.side_effect_summary

    def test_output_has_queue_path(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            result = _run(_good_args(queue_path=str(qp)))
            assert result.output["queue_path"] == str(qp)

    def test_all_platforms_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            for platform in [
                "twitter", "linkedin", "newsletter",
                "blog", "substack", "ghost",
            ]:
                _run(_good_args(queue_path=str(qp), platform=platform))
            lines = qp.read_text().strip().splitlines()
            assert len(lines) == 6

    def test_reply_to_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            _run(_good_args(queue_path=str(qp), reply_to="parent-123"))
            record = json.loads(qp.read_text().strip())
            assert record["reply_to"] == "parent-123"

    def test_directory_created_if_missing(self):
        with tempfile.TemporaryDirectory() as td:
            nested = Path(td) / "deep" / "path" / "queue.jsonl"
            _run(_good_args(queue_path=str(nested)))
            assert nested.exists()

    def test_side_effects_is_external(self):
        assert PublishScheduleTool().side_effects == "external"

    def test_version_is_bare_1(self):
        assert PublishScheduleTool().version == "1"

    def test_request_id_deterministic_per_call(self):
        # Two calls produce different request_ids (different timestamps)
        with tempfile.TemporaryDirectory() as td:
            qp = Path(td) / "queue.jsonl"
            r1 = _run(_good_args(queue_path=str(qp)))
            r2 = _run(_good_args(queue_path=str(qp)))
            # Two records with different IDs, since timestamps differ
            assert (
                r1.output["request_id"] != r2.output["request_id"]
            )
