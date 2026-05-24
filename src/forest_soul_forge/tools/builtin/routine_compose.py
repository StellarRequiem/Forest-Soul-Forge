"""``routine_compose.v1`` — ADR-0091 Phase C routine envelope composer.

Composes deterministic routine envelopes (vacation_mode,
morning_sequence, focus_mode, sleep_mode, custom) into a queue
file at ``data/d5/routine_queue.jsonl`` for operator pickup OR
forest-home-assistant connector consumption. The connector (when
installed) reads the queue, applies routines, writes
``home_state_snapshot`` attestations back into memory.

side_effects=filesystem. The actuator-genre external ceiling
permits this; ``filesystem_always_human_approval`` rule gates each
queue update so the operator approves before any routine lands in
the queue. Same two-layer pattern as time_steward's
schedule_reminder.v1 + spaced_repetition_pilot's
spaced_repetition_schedule.v1 — filesystem class + YELLOW
posture.

## Routine envelope shape

Each routine envelope is one JSONL record:

  {
    "routine_id":      str (derived sha256[:12]),
    "routine_kind":    str,        # vacation_mode / morning_sequence /
                                    # focus_mode / sleep_mode / custom
    "name":            str,        # operator-facing label
    "scheduled_for":   str (ISO),  # fire window start (Pacific-zoned)
    "fire_window_min": int,        # minutes the connector may delay
    "actions": [
      {"device_id": str, "command": str, "args": {...}},
      ...
    ],
    "scope":           str,        # rooms list or "all"
    "source_snapshot_id": str,     # the home_state_snapshot the
                                    # envelope was composed against
    "operator_reason": str,        # operator-supplied prose
    "attestor":        str,        # instance_id of routine_composer
    "agent_role":      str,
    "queued_at":       str (ISO),
  }

Operator approves each routine before queueing (YELLOW posture).
The queue is append-only; connectors mark routines as fired by
writing a separate ``routine_fired:<id>`` memory attestation, not
by rewriting the queue.

## Inputs

  routine_kind (str, required): one of vacation_mode /
    morning_sequence / focus_mode / sleep_mode / custom
  name (str, required): operator-facing label.
  scheduled_for (str ISO, required): fire window start (treated
    as Pacific-zoned per CLAUDE.md).
  actions (list[dict], required): per-device action records.
  scope (str, optional): "all" OR comma-separated room list.
    Default "all".
  fire_window_minutes (int, optional): max allowed delay between
    scheduled_for and actual fire. Default 10.
  source_snapshot_id (str, optional): home_state_snapshot
    memory entry_id that shaped the routine.
  operator_reason (str, optional): audit-readable prose.
  queue_path (str, optional): override queue path; tests pass a
    fixture path; production callers omit.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_DEFAULT_QUEUE_PATH = Path("data/d5/routine_queue.jsonl")
_MAX_NAME_LEN = 200
_MAX_REASON_LEN = 2000
_MAX_ACTIONS = 100
_MAX_DEVICE_ID_LEN = 200
_MAX_COMMAND_LEN = 100
_MAX_ROOMS_LEN = 1000
_VALID_KINDS = {
    "vacation_mode", "morning_sequence", "focus_mode",
    "sleep_mode", "custom",
}
_DEFAULT_FIRE_WINDOW = 10
_MIN_FIRE_WINDOW = 0
_MAX_FIRE_WINDOW = 60 * 24


class RoutineComposeTool:
    """Compose a routine envelope + append to the queue file."""

    name = "routine_compose"
    version = "1"
    side_effects = "filesystem"

    def validate(self, args: dict[str, Any]) -> None:
        kind = args.get("routine_kind")
        if not isinstance(kind, str) or kind not in _VALID_KINDS:
            raise ToolValidationError(
                f"routine_kind must be one of {sorted(_VALID_KINDS)}"
            )

        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolValidationError("name is required")
        if len(name) > _MAX_NAME_LEN:
            raise ToolValidationError(
                f"name must be <= {_MAX_NAME_LEN} chars"
            )

        sf = args.get("scheduled_for")
        if not isinstance(sf, str) or not sf.strip():
            raise ToolValidationError("scheduled_for is required")
        try:
            _parse_iso(sf)
        except ValueError as e:
            raise ToolValidationError(
                f"scheduled_for not parseable: {e}"
            )

        actions = args.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ToolValidationError(
                "actions must be a non-empty list"
            )
        if len(actions) > _MAX_ACTIONS:
            raise ToolValidationError(
                f"actions must have <= {_MAX_ACTIONS} entries"
            )
        for i, a in enumerate(actions):
            if not isinstance(a, dict):
                raise ToolValidationError(
                    f"actions[{i}] must be an object"
                )
            did = a.get("device_id")
            if not isinstance(did, str) or not did.strip():
                raise ToolValidationError(
                    f"actions[{i}].device_id is required"
                )
            if len(did) > _MAX_DEVICE_ID_LEN:
                raise ToolValidationError(
                    f"actions[{i}].device_id must be <= "
                    f"{_MAX_DEVICE_ID_LEN} chars"
                )
            cmd = a.get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                raise ToolValidationError(
                    f"actions[{i}].command is required"
                )
            if len(cmd) > _MAX_COMMAND_LEN:
                raise ToolValidationError(
                    f"actions[{i}].command must be <= "
                    f"{_MAX_COMMAND_LEN} chars"
                )
            ca = a.get("args")
            if ca is not None and not isinstance(ca, dict):
                raise ToolValidationError(
                    f"actions[{i}].args must be an object when supplied"
                )

        scope = args.get("scope")
        if scope is not None:
            if not isinstance(scope, str):
                raise ToolValidationError(
                    "scope must be a string when supplied"
                )
            if len(scope) > _MAX_ROOMS_LEN:
                raise ToolValidationError(
                    f"scope must be <= {_MAX_ROOMS_LEN} chars"
                )

        fw = args.get("fire_window_minutes")
        if fw is not None:
            if not isinstance(fw, int):
                raise ToolValidationError(
                    "fire_window_minutes must be an integer when supplied"
                )
            if fw < _MIN_FIRE_WINDOW or fw > _MAX_FIRE_WINDOW:
                raise ToolValidationError(
                    f"fire_window_minutes must be in "
                    f"[{_MIN_FIRE_WINDOW}, {_MAX_FIRE_WINDOW}]"
                )

        reason = args.get("operator_reason")
        if reason is not None:
            if not isinstance(reason, str):
                raise ToolValidationError(
                    "operator_reason must be a string"
                )
            if len(reason) > _MAX_REASON_LEN:
                raise ToolValidationError(
                    f"operator_reason must be <= {_MAX_REASON_LEN} chars"
                )

        for k in ("source_snapshot_id", "queue_path"):
            v = args.get(k)
            if v is not None and not isinstance(v, str):
                raise ToolValidationError(f"{k} must be a string")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        kind = args["routine_kind"]
        name = args["name"].strip()
        sf = args["scheduled_for"].strip()
        # normalize tz-aware (preserves operator-supplied offset)
        sf_dt = _parse_iso(sf)
        scheduled_for_norm = sf_dt.isoformat(timespec="seconds")

        actions = [
            {
                "device_id": a["device_id"],
                "command":   a["command"],
                "args":      dict(a.get("args") or {}),
            }
            for a in args["actions"]
        ]
        scope = args.get("scope") or "all"
        fire_window = int(
            args.get("fire_window_minutes") or _DEFAULT_FIRE_WINDOW
        )
        source_snapshot_id = args.get("source_snapshot_id") or ""
        operator_reason = args.get("operator_reason") or ""

        queue_path = Path(
            args.get("queue_path") or _DEFAULT_QUEUE_PATH
        )
        queue_path.parent.mkdir(parents=True, exist_ok=True)

        queued_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        routine_id = _derive_id(kind, name, scheduled_for_norm, actions)

        record = {
            "routine_id":         routine_id,
            "routine_kind":       kind,
            "name":               name,
            "scheduled_for":      scheduled_for_norm,
            "fire_window_min":    fire_window,
            "actions":            actions,
            "scope":              scope,
            "source_snapshot_id": source_snapshot_id,
            "operator_reason":    operator_reason,
            "attestor":           ctx.instance_id,
            "agent_role":         ctx.role,
            "queued_at":          queued_at,
        }

        try:
            with queue_path.open("a") as f:
                f.write(json.dumps(record, sort_keys=True))
                f.write("\n")
        except OSError as e:
            raise ToolValidationError(
                f"could not append to routine_queue {queue_path}: {e}"
            )

        body = {
            "routine_id":      routine_id,
            "routine_kind":    kind,
            "name":            name,
            "scheduled_for":   scheduled_for_norm,
            "fire_window_min": fire_window,
            "action_count":    len(actions),
            "scope":           scope,
            "queue_path":      str(queue_path),
            "queued_at":       queued_at,
        }
        return ToolResult(
            output=body,
            metadata={
                "routine_id":     routine_id,
                "routine_kind":   kind,
                "scheduled_for":  scheduled_for_norm,
                "action_count":   len(actions),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"queued routine {routine_id} ({kind}) "
                f"for {scheduled_for_norm}: {len(actions)} action(s)"
            ),
        )


def _parse_iso(s: str) -> datetime:
    raw = s.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _derive_id(
    kind: str, name: str, scheduled_for: str, actions: list[dict[str, Any]],
) -> str:
    action_blob = "|".join(
        f"{a['device_id']}:{a['command']}"
        for a in sorted(actions, key=lambda x: (x["device_id"], x["command"]))
    )
    blob = f"{kind}|{name}|{scheduled_for}|{action_blob}"
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"rt_{digest[:12]}"
