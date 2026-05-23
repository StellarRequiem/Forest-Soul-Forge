"""``schedule_reminder.v1`` — ADR-0087 Phase B reminder substrate.

Creates a reminder record in ``data/d2/reminders.jsonl`` keyed by
ISO fire timestamp. Read by future forest-mail / forest-slack /
desktop-notification connectors that operate as scheduled tasks on
the ADR-0041 substrate; absent that, the file is queryable by the
operator directly.

Why a JSONL file, not a `scheduled_task_state` row? The scheduler's
task table holds ENGINE state (next_run_at, circuit_breaker_open,
budget). Reminders are operator-facing facts — when should this
fire, what should it say, where should it go. Different table, but
the operator authors both via the same substrate path eventually.
For Phase B's MVP, a flat JSONL keeps the value-prop legible:
"set a reminder" → "write a record" → "operator can list / cancel".

side_effects=filesystem. The actuator genre's external ceiling
permits this; ``filesystem_always_human_approval`` rule makes the
creation operator-gated at dispatch (the fire itself runs unattended
when a future connector picks the record up).

ADR-0087 Decision 3 — schedule_reminder is filesystem-class; the
sibling calendar_block.v1 is external-class. Different ceilings,
different connector dependencies, same time_steward kit.
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


_DEFAULT_REMINDERS_PATH = Path("data/d2/reminders.jsonl")
_MAX_MESSAGE_LEN = 2000
_MAX_CHANNEL_LEN = 100
_VALID_CHANNELS = {
    "memory",       # write a memory attestation when fire time arrives
    "email",        # operator email (forest-mail connector)
    "slack",        # operator Slack (forest-slack connector)
    "desktop",      # desktop notification
    "audit",        # only emit an audit event (debug / smoke)
}


class ScheduleReminderTool:
    """Append a reminder record to the D2 reminders ledger.

    Args:
      fire_at (str, required): ISO-8601 timestamp when the reminder
        should fire. MUST be in the future (relative to call time).
        Pacific-time conventions per CLAUDE.md — the operator can
        write '2026-05-24T09:00:00-07:00' or any timezone-aware ISO.
      message (str, required): the reminder text (1-2000 chars).
      channel (str, optional): delivery surface — one of memory /
        email / slack / desktop / audit. Default "memory" (always
        works without external connectors).
      reminders_path (str, optional): override ledger path. Tests
        pass a fixture path; production callers omit.

    Output:
      {
        "reminder_id":   str,         # sha-derived stable id
        "fire_at":       str (ISO),
        "channel":       str,
        "message":       str,
        "appended_at":   str (ISO),
        "ledger_path":   str,
      }
    """

    name = "schedule_reminder"
    version = "1"
    side_effects = "filesystem"

    def validate(self, args: dict[str, Any]) -> None:
        fire_at = args.get("fire_at")
        if not isinstance(fire_at, str) or not fire_at.strip():
            raise ToolValidationError("fire_at is required (ISO timestamp)")
        try:
            parsed = _parse_iso(fire_at)
        except ValueError as e:
            raise ToolValidationError(f"fire_at not parseable: {e}")
        if parsed.timestamp() <= time.time():
            raise ToolValidationError(
                "fire_at must be in the future "
                "(reminders that already fired are noise)"
            )

        message = args.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ToolValidationError("message is required")
        if len(message) > _MAX_MESSAGE_LEN:
            raise ToolValidationError(
                f"message must be <= {_MAX_MESSAGE_LEN} chars; "
                f"got {len(message)}"
            )

        channel = args.get("channel", "memory")
        if not isinstance(channel, str):
            raise ToolValidationError("channel must be a string")
        if channel not in _VALID_CHANNELS:
            raise ToolValidationError(
                f"channel must be one of {sorted(_VALID_CHANNELS)}; "
                f"got {channel!r}"
            )
        if len(channel) > _MAX_CHANNEL_LEN:
            raise ToolValidationError(
                f"channel too long ({len(channel)} chars)"
            )

        rp = args.get("reminders_path")
        if rp is not None and not isinstance(rp, str):
            raise ToolValidationError(
                "reminders_path must be a string"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        fire_at_raw = args["fire_at"]
        fire_dt = _parse_iso(fire_at_raw)
        message = args["message"]
        channel = args.get("channel", "memory")
        ledger_path = Path(
            args.get("reminders_path") or _DEFAULT_REMINDERS_PATH
        )

        ledger_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        reminder_id = _derive_reminder_id(
            fire_dt.isoformat(), message, channel, now.isoformat()
        )
        record = {
            "reminder_id":  reminder_id,
            "fire_at":      fire_dt.isoformat(),
            "channel":      channel,
            "message":      message,
            "appended_at":  now.isoformat(timespec="seconds"),
            "attestor":     ctx.instance_id,
            "agent_role":   ctx.role,
        }

        try:
            with ledger_path.open("a") as f:
                f.write(json.dumps(record, sort_keys=True))
                f.write("\n")
        except OSError as e:
            raise ToolValidationError(
                f"could not append to reminders ledger {ledger_path}: {e}"
            )

        body = {
            "reminder_id":  reminder_id,
            "fire_at":      fire_dt.isoformat(),
            "channel":      channel,
            "message":      message,
            "appended_at":  record["appended_at"],
            "ledger_path":  str(ledger_path),
        }
        return ToolResult(
            output=body,
            metadata={
                "reminder_id":  reminder_id,
                "channel":      channel,
                "fire_at":      fire_dt.isoformat(),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"scheduled reminder {reminder_id} for "
                f"{fire_dt.isoformat()} via {channel}"
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


_REMINDER_ID_RE = re.compile(r"[^a-zA-Z0-9]+")


def _derive_reminder_id(
    fire_at: str, message: str, channel: str, ts: str,
) -> str:
    """Stable short id from fire-time + message + channel + create-ts.

    Not cryptographic. Operators read these in the ledger;
    collision risk is negligible at human-scale reminder counts.
    """
    import hashlib

    blob = f"{fire_at}|{channel}|{message}|{ts}"
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"rem_{digest[:12]}"
