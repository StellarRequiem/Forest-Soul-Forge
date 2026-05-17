"""ADR-0064 T1 — TelemetryEvent canonical shape + integrity hashing.

A TelemetryEvent describes one observation the operator's machine
made: a process spawned, a file changed, an auth attempt happened.
The shape is intentionally narrow (8 enum types) and intentionally
deterministic (canonical_form returns the same bytes for the same
event regardless of dict insertion order or whitespace).

Why canonical form matters: external ingestors compute the integrity
hash before the event reaches the daemon. The daemon recomputes on
receipt to detect tampering in flight. If canonical_form weren't
deterministic, every machine would compute a different hash for the
same event and the chain of custody breaks.

What's NOT here:
  - Storage. See store.py (TelemetryStore + SqliteTelemetryStore).
  - Retention rules. See retention.py (classify_retention).
  - Adapter contracts. Ship in T2.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


# ADR-0064 Decision 2 — closed event-type enum.
# Adapters that don't fit one of these emit `sensor_reading` with
# the kind stuffed into `payload`. Keeping this list closed means
# downstream consumers (anomaly_ace, threat_intel_curator) can write
# branching logic without a default-case fall-through that masks
# unknown event_types as benign.
EVENT_TYPES: frozenset[str] = frozenset({
    "process_spawn",
    "process_exit",
    "network_connection",
    "file_change",
    "auth_event",
    "log_line",
    "policy_decision",
    "sensor_reading",
})

# Three-level severity. NOT debug/info/warn/error — that's a logger
# vocabulary, not a SOC vocabulary. critical means "this should be on
# an operator's screen now".
SEVERITIES: frozenset[str] = frozenset({"info", "warn", "critical"})

# ADR-0064 Decision 4 — three retention classes.
# ephemeral 7d for high-volume noise; standard 90d default;
# security_relevant 365d for the events the operator will need
# during an audit or incident postmortem.
RETENTION_CLASSES: frozenset[str] = frozenset({
    "ephemeral",
    "standard",
    "security_relevant",
})


class TelemetryEventError(ValueError):
    """Raised when a TelemetryEvent's invariants are violated.

    Distinct from ValueError so callers can catch this class
    specifically without swallowing unrelated ValueErrors from
    json/datetime/etc.
    """


@dataclass(frozen=True)
class TelemetryEvent:
    """Canonical telemetry event. See ADR-0064 Decision 1.

    Field order matches the SQL column order in store.py's
    SQLITE_SCHEMA_V1 so the dataclass-to-row mapping is one-to-one
    without an intermediate dict shuffle.
    """

    event_id: str
    timestamp: str           # ISO 8601 with timezone offset
    source: str              # what produced this; freeform string
    event_type: str          # MUST be in EVENT_TYPES
    severity: str            # MUST be in SEVERITIES
    payload: dict[str, Any]  # source-specific; deterministically serializable
    correlation_id: str | None
    integrity_hash: str      # sha256 hex of canonical_form (without event_id+ingested_at)
    ingested_at: str         # ISO 8601 — server-assigned at store-time
    retention_class: str = "standard"   # MUST be in RETENTION_CLASSES

    def __post_init__(self) -> None:
        # Enum validation. Frozen dataclass means we can't mutate
        # the field, so any normalization (e.g., lowercase) has to
        # happen before construction.
        if self.event_type not in EVENT_TYPES:
            raise TelemetryEventError(
                f"event_type {self.event_type!r} not in EVENT_TYPES; "
                f"allowed: {sorted(EVENT_TYPES)}"
            )
        if self.severity not in SEVERITIES:
            raise TelemetryEventError(
                f"severity {self.severity!r} not in SEVERITIES; "
                f"allowed: {sorted(SEVERITIES)}"
            )
        if self.retention_class not in RETENTION_CLASSES:
            raise TelemetryEventError(
                f"retention_class {self.retention_class!r} not in "
                f"RETENTION_CLASSES; allowed: {sorted(RETENTION_CLASSES)}"
            )
        # Basic timestamp shape check. Full ISO-8601 parsing is left
        # to the consumer (datetime.fromisoformat); we just reject
        # empty strings + obvious non-timestamps so the store doesn't
        # ingest unsortable garbage.
        if not isinstance(self.timestamp, str) or len(self.timestamp) < 10:
            raise TelemetryEventError(
                f"timestamp must be a non-trivial ISO 8601 string; "
                f"got {self.timestamp!r}"
            )
        if not isinstance(self.ingested_at, str) or len(self.ingested_at) < 10:
            raise TelemetryEventError(
                f"ingested_at must be a non-trivial ISO 8601 string; "
                f"got {self.ingested_at!r}"
            )
        if not isinstance(self.source, str) or not self.source.strip():
            raise TelemetryEventError("source must be non-empty string")
        if not isinstance(self.payload, dict):
            raise TelemetryEventError(
                f"payload must be a dict; got {type(self.payload).__name__}"
            )
        # integrity_hash is sha256 hex = 64 lowercase hex chars.
        if not isinstance(self.integrity_hash, str) or len(self.integrity_hash) != 64:
            raise TelemetryEventError(
                f"integrity_hash must be 64-char sha256 hex; "
                f"got {self.integrity_hash!r} (len={len(self.integrity_hash)})"
            )


def canonical_form(
    *,
    timestamp: str,
    source: str,
    event_type: str,
    severity: str,
    payload: dict[str, Any],
    correlation_id: str | None,
    retention_class: str = "standard",
) -> bytes:
    """Return the bytes that get hashed for integrity verification.

    Excludes event_id (server-assigned) and ingested_at
    (server-assigned). This is what an external ingestor sees BEFORE
    submitting to the daemon, so the ingestor can compute the hash
    independently + the daemon can verify on receipt.

    JSON encoding rules:
      - sort_keys=True (deterministic ordering for nested dicts)
      - separators=(',', ':')  (no incidental whitespace)
      - ensure_ascii=False  (UTF-8 throughout — adapters may emit
        non-ASCII filenames, usernames, log content)

    Returns BYTES (UTF-8 encoded), not a str — sha256 wants bytes
    and we don't want to leave the encoding step to callers who
    might use the wrong encoding.
    """
    # The order of keys in `doc` doesn't matter for correctness
    # because sort_keys=True normalizes; we still construct
    # alphabetically for readability of any debug dumps.
    doc = {
        "correlation_id": correlation_id,
        "event_type": event_type,
        "payload": payload,
        "retention_class": retention_class,
        "severity": severity,
        "source": source,
        "timestamp": timestamp,
    }
    return json.dumps(
        doc,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_integrity_hash(
    *,
    timestamp: str,
    source: str,
    event_type: str,
    severity: str,
    payload: dict[str, Any],
    correlation_id: str | None,
    retention_class: str = "standard",
) -> str:
    """Convenience wrapper: canonical_form → sha256 hex digest.

    Used by both the ingest path (compute before insert) and the
    verify path (compute from stored row, compare against stored
    integrity_hash). Mismatch surfaces as `tamper_suspected` per the
    ADR-0073 sealed-segment verifier pattern.
    """
    return hashlib.sha256(
        canonical_form(
            timestamp=timestamp,
            source=source,
            event_type=event_type,
            severity=severity,
            payload=payload,
            correlation_id=correlation_id,
            retention_class=retention_class,
        )
    ).hexdigest()
