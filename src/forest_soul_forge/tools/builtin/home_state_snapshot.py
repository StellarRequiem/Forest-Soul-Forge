"""``home_state_snapshot.v1`` — ADR-0091 Phase C state aggregator.

Deterministic aggregator over a list of operator-supplied OR
connector-supplied home_state records. Computes per-room device
rollups + presence indicator + flags stale records. Read-only.

Primary consumer: routine_composer (D5 Phase C) — every routine
envelope is composed against a snapshot so the operator can audit
which state shaped which routine. home_steward + home_sentinel
follow-ups also use it for cross-validating LLM-composed reports
against the deterministic rollup.

## Inputs

  records (list[dict], required): per-device state records. Each:
    - device_id (str, required)
    - room (str, required)
    - state (str, required): operator-vocabulary device state
      ("on", "off", "open", "closed", "37C", "armed", etc.)
    - observed_at (str ISO, required): when the reading was taken
      (treated as Pacific-zoned per CLAUDE.md)
    - device_kind (str, optional): hint for room rollup
      ("light", "lock", "sensor", "thermostat", "presence")
    - value (number, optional): numeric value where applicable
      (energy_warden also uses the same shape — see
      energy_anomaly_scan.v1)
  reference_time (str ISO, optional): "now" relative to which
    stale_window_minutes is applied. Default ``now`` UTC.
  stale_window_minutes (int, optional): records older than this
    (relative to reference_time) are flagged stale. Default 60.
  window_slug (str, optional): operator slug for the snapshot.

## Output

  {
    "window_slug":  str,
    "built_at":     str (ISO Z),
    "device_count": int,
    "room_count":   int,
    "stale_count":  int,
    "presence_flag": str,   # "present" / "absent" / "unknown"
    "rooms": [
      {
        "room": str,
        "device_count": int,
        "active_devices": [device_id, ...],
        "inactive_devices": [device_id, ...],
        "stale_devices": [device_id, ...],
        "device_kinds": {kind: count, ...},
        "thermostat_readings": [{device_id, value, unit?}, ...],
      },
      ...
    ],
    "anomaly_hints": [str, ...],  # human-readable per-room flags
  }

side_effects=read_only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_RECORDS = 500
_MAX_DEVICE_ID_LEN = 200
_MAX_STATE_LEN = 100
_MAX_ROOM_LEN = 100
_DEFAULT_STALE_MINUTES = 60
_MIN_STALE = 1
_MAX_STALE = 60 * 24 * 30
_ACTIVE_STATES = {"on", "open", "armed", "running", "playing", "unlocked"}
_INACTIVE_STATES = {"off", "closed", "disarmed", "stopped", "paused", "locked"}
_PRESENCE_KINDS = {"presence", "presence_sensor", "occupancy"}
_PRESENT_STATES = {"home", "present", "detected", "on"}
_ABSENT_STATES = {"away", "absent", "not_detected", "off"}


class HomeStateSnapshotTool:
    """Aggregate device records into per-room rollups."""

    name = "home_state_snapshot"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        recs = args.get("records")
        if not isinstance(recs, list) or not recs:
            raise ToolValidationError(
                "records must be a non-empty list"
            )
        if len(recs) > _MAX_RECORDS:
            raise ToolValidationError(
                f"records must have <= {_MAX_RECORDS} entries"
            )
        for i, r in enumerate(recs):
            if not isinstance(r, dict):
                raise ToolValidationError(
                    f"records[{i}] must be an object"
                )
            for k in ("device_id", "room", "state", "observed_at"):
                v = r.get(k)
                if not isinstance(v, str) or not v.strip():
                    raise ToolValidationError(
                        f"records[{i}].{k} is required"
                    )
            if len(r["device_id"]) > _MAX_DEVICE_ID_LEN:
                raise ToolValidationError(
                    f"records[{i}].device_id must be <= "
                    f"{_MAX_DEVICE_ID_LEN} chars"
                )
            if len(r["room"]) > _MAX_ROOM_LEN:
                raise ToolValidationError(
                    f"records[{i}].room must be <= {_MAX_ROOM_LEN} chars"
                )
            if len(r["state"]) > _MAX_STATE_LEN:
                raise ToolValidationError(
                    f"records[{i}].state must be <= {_MAX_STATE_LEN} chars"
                )
            try:
                _parse_iso(r["observed_at"])
            except ValueError as e:
                raise ToolValidationError(
                    f"records[{i}].observed_at not parseable: {e}"
                )
            v = r.get("value")
            if v is not None and not isinstance(v, (int, float)):
                raise ToolValidationError(
                    f"records[{i}].value must be a number when supplied"
                )
            for k in ("device_kind",):
                vv = r.get(k)
                if vv is not None and not isinstance(vv, str):
                    raise ToolValidationError(
                        f"records[{i}].{k} must be a string"
                    )

        for k, lo, hi in (
            ("stale_window_minutes", _MIN_STALE, _MAX_STALE),
        ):
            v = args.get(k)
            if v is not None:
                if not isinstance(v, int):
                    raise ToolValidationError(
                        f"{k} must be an integer when supplied"
                    )
                if v < lo or v > hi:
                    raise ToolValidationError(
                        f"{k} must be in [{lo}, {hi}]"
                    )

        rt = args.get("reference_time")
        if rt is not None:
            if not isinstance(rt, str):
                raise ToolValidationError("reference_time must be a string")
            try:
                _parse_iso(rt)
            except ValueError as e:
                raise ToolValidationError(
                    f"reference_time not parseable: {e}"
                )

        slug = args.get("window_slug")
        if slug is not None and not isinstance(slug, str):
            raise ToolValidationError("window_slug must be a string")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        recs = list(args["records"])
        slug = args.get("window_slug") or ""
        stale_minutes = int(
            args.get("stale_window_minutes") or _DEFAULT_STALE_MINUTES
        )
        if args.get("reference_time"):
            ref = _parse_iso(args["reference_time"])
        else:
            ref = datetime.now(timezone.utc)
        stale_cutoff = ref - timedelta(minutes=stale_minutes)

        rooms: dict[str, dict[str, Any]] = {}
        stale_count = 0
        presence_signal = "unknown"
        anomaly_hints: list[str] = []

        for r in recs:
            room = r["room"]
            did = r["device_id"]
            state = r["state"].strip().lower()
            kind = (r.get("device_kind") or "").strip().lower()
            obs_dt = _parse_iso(r["observed_at"])

            bucket = rooms.setdefault(room, {
                "room":               room,
                "device_count":       0,
                "active_devices":     [],
                "inactive_devices":   [],
                "stale_devices":      [],
                "device_kinds":       {},
                "thermostat_readings": [],
            })
            bucket["device_count"] += 1
            if kind:
                bucket["device_kinds"][kind] = (
                    bucket["device_kinds"].get(kind, 0) + 1
                )

            if obs_dt < stale_cutoff:
                bucket["stale_devices"].append(did)
                stale_count += 1

            if state in _ACTIVE_STATES:
                bucket["active_devices"].append(did)
            elif state in _INACTIVE_STATES:
                bucket["inactive_devices"].append(did)

            if kind in _PRESENCE_KINDS:
                if state in _PRESENT_STATES:
                    presence_signal = "present"
                elif state in _ABSENT_STATES and presence_signal == "unknown":
                    presence_signal = "absent"

            if kind == "thermostat" and isinstance(r.get("value"), (int, float)):
                bucket["thermostat_readings"].append({
                    "device_id": did,
                    "value":     round(float(r["value"]), 4),
                    "unit":      "C" if "C" in r["state"] else (
                        "F" if "F" in r["state"] else ""
                    ),
                })

        for room_name, b in rooms.items():
            if b["stale_devices"]:
                anomaly_hints.append(
                    f"{room_name}: {len(b['stale_devices'])} stale device(s)"
                )
            if (b["device_kinds"].get("lock", 0) > 0
                and any(
                    rid in b["active_devices"]
                    for rid in [did for did in b["active_devices"]]
                )):
                # lock active=unlocked is noteworthy when presence=absent
                if presence_signal == "absent":
                    anomaly_hints.append(
                        f"{room_name}: lock unlocked while household absent"
                    )

        sorted_rooms = sorted(
            rooms.values(), key=lambda r: r["room"],
        )
        for r in sorted_rooms:
            r["active_devices"].sort()
            r["inactive_devices"].sort()
            r["stale_devices"].sort()
            r["thermostat_readings"].sort(
                key=lambda t: t["device_id"],
            )

        body = {
            "window_slug":   slug,
            "built_at":      datetime.now(timezone.utc)
                                        .replace(tzinfo=None)
                                        .isoformat(timespec="seconds")
                                        + "Z",
            "device_count":  len(recs),
            "room_count":    len(rooms),
            "stale_count":   stale_count,
            "stale_window_minutes": stale_minutes,
            "presence_flag": presence_signal,
            "rooms":         sorted_rooms,
            "anomaly_hints": anomaly_hints,
        }
        return ToolResult(
            output=body,
            metadata={
                "window_slug":   slug,
                "device_count":  len(recs),
                "room_count":    len(rooms),
                "stale_count":   stale_count,
                "presence_flag": presence_signal,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"snapshot {slug!r}: {len(recs)} devices / "
                f"{len(rooms)} rooms / {stale_count} stale / "
                f"presence={presence_signal}"
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
