"""ADR-0064 T2 reference adapter — macOS unified log stream.

Drives `log stream --style ndjson --predicate <expr>` and parses
each line into a TelemetryEvent. Read-only — `log stream` is a
read-only consumer; we never write to the unified log.

Why macOS unified log first: every modern macOS host has it, no
extra install, and it covers the SOC's most operationally useful
signals (auth, network, process, security policy decisions). It's
also a good stress test for the adapter contract because the JSON
shape is intricate (per-message subsystem + category + activity_id
+ thread_id + sender + format + arguments) and the volume can
spike (boot can produce thousands of entries per second).

Default predicate: limited to security-relevant subsystems so we
don't drown the operator in noise. The predicate is overridable
via __init__(predicate=...) for adapters tuned to a specific
investigation.

Parsing rules:
  - severity mapping:
        eventMessage missing OR messageType in {"Default","Info"} → info
        messageType == "Error" → warn
        messageType == "Fault" → critical
    The unified-log severity vocabulary doesn't map cleanly to ours
    so we collapse Default+Info; the operator can override per-event
    via retention_override.
  - event_type mapping:
        subsystem == "com.apple.securityd"           → auth_event
        subsystem == "com.apple.authd"               → auth_event
        subsystem == "com.apple.xprotect"            → policy_decision
        subsystem == "com.apple.networking.*"        → log_line
        else                                          → log_line
    Anything that doesn't fit a closed type lands as log_line +
    the original subsystem in payload — downstream consumers can
    branch on subsystem rather than re-deriving from the message.
  - retention override:
        event_type IN {auth_event, policy_decision} → security_relevant
        (the central classifier would catch these anyway; the override
         is explicit so future-us debugging the chain sees the
         adapter's intent, not coincidence)

NOT in scope:
  - Backfill mode (`log show --start ...`). T2 ships stream-mode
    only; backfill is a later operator-driven path.
  - Per-event signing keys. The adapter uses the daemon's existing
    ADR-0049 chain signature when the batch lands; per-event
    signatures would require a per-adapter key, deferred to a
    future ADR if it actually matters.
"""
from __future__ import annotations

import json
from typing import Any

from ..adapter import Adapter
from ..events import TelemetryEvent


DEFAULT_PREDICATE = (
    'subsystem == "com.apple.securityd" '
    'OR subsystem == "com.apple.authd" '
    'OR subsystem == "com.apple.xprotect" '
    'OR (messageType == "Error" OR messageType == "Fault")'
)


# Subsystem prefix → our event_type. Longest-match wins.
SUBSYSTEM_TYPE_MAP: dict[str, str] = {
    "com.apple.securityd":      "auth_event",
    "com.apple.authd":          "auth_event",
    "com.apple.xprotect":       "policy_decision",
    "com.apple.opendirectoryd": "auth_event",
}


# message_type from unified log → our severity.
MESSAGE_TYPE_SEVERITY: dict[str, str] = {
    "Default":  "info",
    "Info":     "info",
    "Debug":    "info",
    "Error":    "warn",
    "Fault":    "critical",
}


class MacosUnifiedLogAdapter(Adapter):
    """Stream from macOS unified log via `log stream --style ndjson`."""

    SOURCE = "macos_unified_log"

    def __init__(self, *, predicate: str = DEFAULT_PREDICATE) -> None:
        if not isinstance(predicate, str) or not predicate.strip():
            raise ValueError("predicate must be a non-empty string")
        self.predicate = predicate

    def command(self) -> list[str]:
        # --style ndjson is the documented machine-readable mode.
        # --info / --debug omitted intentionally — predicate handles
        # severity filtering and we don't want debug-level firehose
        # in the default config.
        return [
            "log", "stream",
            "--style", "ndjson",
            "--predicate", self.predicate,
        ]

    def parse(self, line: str) -> TelemetryEvent | None:
        if not line or not line.strip():
            return None
        # `log stream --style ndjson` occasionally emits a meta-line
        # like "Filtering the log data using ..." before the first
        # event. These aren't JSON; drop quietly.
        if not line.startswith("{"):
            return None
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            # Malformed line — silently drop per the adapter contract
            # (parse MUST NOT raise; tests pin this).
            return None
        if not isinstance(doc, dict):
            return None

        # Required-ish fields. Missing timestamp = unusable.
        timestamp = doc.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            return None

        subsystem = str(doc.get("subsystem") or "")
        message_type = str(doc.get("messageType") or "")
        message = str(doc.get("eventMessage") or "")
        process = str(doc.get("processImagePath") or "")
        pid = doc.get("processID")

        event_type = self._classify_event_type(subsystem)
        severity = MESSAGE_TYPE_SEVERITY.get(message_type, "info")

        # Payload carries everything downstream consumers might need
        # without forcing them to re-parse the unified log shape.
        payload: dict[str, Any] = {
            "subsystem": subsystem,
            "category": str(doc.get("category") or ""),
            "message_type": message_type,
            "message": message,
            "process": process,
            "pid": pid,
            "thread_id": doc.get("threadID"),
            "activity_id": doc.get("activityIdentifier"),
        }

        # correlation_id: unified log activity_id when present. Gives
        # consumers a per-activity chain to walk.
        activity_id = doc.get("activityIdentifier")
        correlation_id = (
            str(activity_id) if activity_id not in (None, 0, "0") else None
        )

        return self.make_event(
            timestamp=timestamp,
            event_type=event_type,
            severity=severity,
            payload=payload,
            correlation_id=correlation_id,
            # Leave retention_class at the default; retention_override
            # below tightens for security-relevant types.
        )

    def retention_override(self, event: TelemetryEvent) -> str | None:
        """auth + policy events get security_relevant regardless of
        severity. The central classifier would catch these anyway,
        but the explicit override means the audit chain shows
        adapter intent + decouples us from changes to the central
        classifier."""
        if event.event_type in ("auth_event", "policy_decision"):
            return "security_relevant"
        return None

    @staticmethod
    def _classify_event_type(subsystem: str) -> str:
        # Exact match first (cheapest); falls through to the default.
        if subsystem in SUBSYSTEM_TYPE_MAP:
            return SUBSYSTEM_TYPE_MAP[subsystem]
        # Networking is the only family we collapse by prefix today.
        if subsystem.startswith("com.apple.networking"):
            return "log_line"
        return "log_line"
