"""``calendar_block.v1`` — ADR-0087 Phase B calendar surface.

Creates / declines calendar events via the operator's
forest-calendar connector. Graceful degradation per ADR-0086
Decision 4 pattern: when the connector is absent, the tool
refuses cleanly with "calendar connector not wired" rather than
crashing. When present, the tool writes a calendar request record
to ``data/d2/calendar_queue.jsonl`` for the connector to dispatch.

side_effects=external. ``external_always_human_approval`` rule
auto-applies requires_human_approval=True on every call — every
calendar create / decline goes through the operator approval
queue regardless of agent posture. The actuator genre's external
ceiling permits this; per-call approval is the load-bearing
safety.

Why a queue file, not a direct connector call? The connector
runs as a separate process and may not be alive at dispatch
time. Queueing keeps the agent surface independent of connector
liveness; the connector picks up the queue at its own cadence
and the audit chain remains coherent regardless.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_DEFAULT_CALENDAR_QUEUE = Path("data/d2/calendar_queue.jsonl")
_CONNECTOR_MARKER = Path("data/connectors/forest-calendar.enabled")
_MAX_SUBJECT_LEN = 500
_MAX_BODY_LEN = 5000
_MAX_ATTENDEES = 50
_VALID_OPERATIONS = {"create", "decline", "cancel"}


class CalendarBlockTool:
    """Queue a calendar operation for the forest-calendar connector.

    Args:
      operation (str, required): one of create / decline / cancel.
      start (str, required for create): ISO-8601 timestamp.
      end (str, required for create): ISO-8601 timestamp (must
        be after start).
      subject (str, required for create): event title.
      body (str, optional): event description / agenda.
      attendees (list[str], optional): email addresses, max 50.
      event_id (str, required for decline / cancel): identifier of
        the existing event the operation targets.
      decline_message (str, optional): for decline; the polite
        message the operator wants surfaced.
      queue_path (str, optional): override queue ledger.
      assume_connector (bool, optional): tests pass True to bypass
        the connector-marker presence check; production callers omit.

    Output:
      {
        "request_id":     str,
        "operation":      str,
        "queued_at":      str (ISO),
        "queue_path":     str,
        "connector_present": bool,
      }

    Refuses cleanly with "calendar connector not wired" when the
    marker file is absent (graceful degradation per ADR-0086
    Decision 4 pattern).
    """

    name = "calendar_block"
    version = "1"
    side_effects = "external"

    def validate(self, args: dict[str, Any]) -> None:
        op = args.get("operation")
        if op not in _VALID_OPERATIONS:
            raise ToolValidationError(
                f"operation must be one of {sorted(_VALID_OPERATIONS)}; "
                f"got {op!r}"
            )

        if op == "create":
            start = args.get("start")
            end = args.get("end")
            if not isinstance(start, str) or not start.strip():
                raise ToolValidationError("start is required for create")
            if not isinstance(end, str) or not end.strip():
                raise ToolValidationError("end is required for create")
            try:
                start_dt = _parse_iso(start)
                end_dt = _parse_iso(end)
            except ValueError as e:
                raise ToolValidationError(
                    f"start/end not parseable: {e}"
                )
            if end_dt <= start_dt:
                raise ToolValidationError("end must be after start")
            if start_dt.timestamp() <= time.time() - 60:
                # 60-second grace window for clock skew + dispatch lag
                raise ToolValidationError(
                    "start must be in the future "
                    "(past events are operator-historical, not creatable)"
                )
            subject = args.get("subject")
            if not isinstance(subject, str) or not subject.strip():
                raise ToolValidationError(
                    "subject is required for create"
                )
            if len(subject) > _MAX_SUBJECT_LEN:
                raise ToolValidationError(
                    f"subject must be <= {_MAX_SUBJECT_LEN} chars"
                )
            body = args.get("body", "")
            if not isinstance(body, str):
                raise ToolValidationError("body must be a string")
            if len(body) > _MAX_BODY_LEN:
                raise ToolValidationError(
                    f"body must be <= {_MAX_BODY_LEN} chars"
                )
            attendees = args.get("attendees", [])
            if not isinstance(attendees, list):
                raise ToolValidationError("attendees must be a list")
            if len(attendees) > _MAX_ATTENDEES:
                raise ToolValidationError(
                    f"attendees count must be <= {_MAX_ATTENDEES}"
                )
            for a in attendees:
                if not isinstance(a, str) or "@" not in a:
                    raise ToolValidationError(
                        f"each attendee must be an email-like string; got {a!r}"
                    )

        if op in ("decline", "cancel"):
            event_id = args.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                raise ToolValidationError(
                    f"event_id is required for {op}"
                )

        if op == "decline":
            msg = args.get("decline_message", "")
            if not isinstance(msg, str):
                raise ToolValidationError(
                    "decline_message must be a string"
                )
            if len(msg) > _MAX_BODY_LEN:
                raise ToolValidationError(
                    f"decline_message must be <= {_MAX_BODY_LEN} chars"
                )

        qp = args.get("queue_path")
        if qp is not None and not isinstance(qp, str):
            raise ToolValidationError("queue_path must be a string")

        ac = args.get("assume_connector")
        if ac is not None and not isinstance(ac, bool):
            raise ToolValidationError(
                "assume_connector must be a boolean"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        op = args["operation"]
        queue_path = Path(
            args.get("queue_path") or _DEFAULT_CALENDAR_QUEUE
        )
        assume_connector = bool(args.get("assume_connector", False))

        connector_present = (
            assume_connector or _CONNECTOR_MARKER.exists()
        )
        if not connector_present:
            raise ToolValidationError(
                "calendar connector not wired — install + enable "
                "forest-calendar (touch data/connectors/forest-calendar.enabled) "
                "before dispatching calendar_block. Graceful degradation "
                "per ADR-0086 Decision 4 pattern."
            )

        queue_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        request_id = _derive_request_id(op, json.dumps(args, sort_keys=True), now.isoformat())

        record = {
            "request_id":   request_id,
            "operation":    op,
            "queued_at":    now.isoformat(timespec="seconds"),
            "attestor":     ctx.instance_id,
            "agent_role":   ctx.role,
            "payload":      _payload_for(op, args),
        }

        try:
            with queue_path.open("a") as f:
                f.write(json.dumps(record, sort_keys=True))
                f.write("\n")
        except OSError as e:
            raise ToolValidationError(
                f"could not append to calendar queue {queue_path}: {e}"
            )

        body = {
            "request_id":        request_id,
            "operation":         op,
            "queued_at":         record["queued_at"],
            "queue_path":        str(queue_path),
            "connector_present": True,
        }
        return ToolResult(
            output=body,
            metadata={
                "request_id": request_id,
                "operation":  op,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"queued calendar {op} request {request_id}"
            ),
        )


def _parse_iso(s: str) -> datetime:
    raw = s.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise ValueError(str(e)) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _payload_for(op: str, args: dict[str, Any]) -> dict[str, Any]:
    if op == "create":
        return {
            "start":     args["start"],
            "end":       args["end"],
            "subject":   args["subject"],
            "body":      args.get("body", ""),
            "attendees": args.get("attendees", []),
        }
    if op == "decline":
        return {
            "event_id":        args["event_id"],
            "decline_message": args.get("decline_message", ""),
        }
    if op == "cancel":
        return {"event_id": args["event_id"]}
    return {}


def _derive_request_id(op: str, blob: str, ts: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{op}|{blob}|{ts}".encode("utf-8")).hexdigest()
    return f"cal_{digest[:12]}"
