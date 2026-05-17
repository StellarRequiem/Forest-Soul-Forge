"""ADR-0064 T2 — Telemetry adapter contract.

An adapter is the bridge between an external data source (macOS
unified log, lsof, fsevents, ...) and the TelemetryStore. The
contract is intentionally narrow:

  1. ``source`` — the string that goes in TelemetryEvent.source.
     Must be allowlisted in config/telemetry_sources.yaml.

  2. ``command`` — an argv list the AdapterIngestor passes to
     subprocess.Popen. Adapter classes return this lazily because
     some adapters need runtime parameterization (e.g., the
     macos_unified_log adapter accepts a predicate string).

  3. ``parse(line)`` — pure function that turns ONE line of the
     subprocess's stdout into ZERO OR ONE TelemetryEvent. Returning
     None lets adapters skip noise (heartbeat lines, source
     boundary markers, parse-fail lines they want to drop quietly).

  4. ``retention_override(event)`` — optional. Returns a retention
     class string OR None to defer to classify_retention. Lets
     adapters express domain knowledge: e.g., the macOS unified
     log adapter knows that auth subsystem events are
     security_relevant even when the severity field is info.

The contract is pure-function except for command(). That makes
adapters trivial to unit-test: feed canned lines into parse(),
assert the resulting TelemetryEvent shape. No subprocess required.

This module owns the ABC + helpers. SubprocessAdapterIngestor (the
one that actually drives Popen) lives in ingestor.py.
"""
from __future__ import annotations

import abc
import uuid
from datetime import datetime, timezone
from typing import Any

from .events import (
    TelemetryEvent,
    TelemetryEventError,
    compute_integrity_hash,
)


class AdapterError(Exception):
    """Raised when an adapter's declared contract is violated:
    bad source name, malformed command, refused-by-allowlist."""


class Adapter(abc.ABC):
    """Abstract base for all telemetry adapters.

    Concrete adapters subclass this and implement four methods.
    Parameterization (predicate strings, allowlist paths, etc.)
    goes through __init__ — the contract methods themselves are
    side-effect-free besides their declared return value.
    """

    # Subclasses MUST set this class attribute. The allowlist loader
    # uses it to verify the adapter class matches the YAML entry.
    SOURCE: str = ""

    @abc.abstractmethod
    def command(self) -> list[str]:
        """Return the argv the ingestor passes to subprocess.Popen.

        Called once per adapter session start. If you need a fresh
        timestamp or fresh predicate per session, compute it here
        rather than caching.
        """

    @abc.abstractmethod
    def parse(self, line: str) -> TelemetryEvent | None:
        """Turn one line of subprocess stdout into a TelemetryEvent.

        Return None to skip the line silently (heartbeat, marker,
        unparseable noise the adapter wants to drop). The ingestor
        treats None as 'not an event' and moves on.

        MUST NOT raise on malformed input — adapters that crash on
        bad lines bring down the ingestor. Return None instead and
        the adapter can log the dropped line elsewhere if it cares.
        """

    def retention_override(self, event: TelemetryEvent) -> str | None:
        """Optional per-event retention class override.

        Default: return None (defer to classify_retention). Subclasses
        override when they have domain knowledge the central
        classifier doesn't (e.g., an adapter that knows certain
        subsystems are always security-relevant regardless of
        severity).
        """
        return None

    # ---- helpers exposed to subclasses ----------------------------------

    def make_event(
        self,
        *,
        timestamp: str,
        event_type: str,
        severity: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        retention_class: str = "standard",
        ingested_at: str | None = None,
    ) -> TelemetryEvent:
        """Construct a TelemetryEvent with auto-computed event_id +
        integrity_hash. Subclasses should use this helper rather than
        constructing TelemetryEvent directly so the hash is always
        computed consistently.

        ``ingested_at`` defaults to utc_now().isoformat() so adapters
        that don't care about ingest-time precision just omit it.
        """
        ingested = ingested_at or datetime.now(timezone.utc).isoformat()
        ih = compute_integrity_hash(
            timestamp=timestamp,
            source=self.SOURCE,
            event_type=event_type,
            severity=severity,
            payload=payload,
            correlation_id=correlation_id,
            retention_class=retention_class,
        )
        return TelemetryEvent(
            event_id=uuid.uuid4().hex,
            timestamp=timestamp,
            source=self.SOURCE,
            event_type=event_type,
            severity=severity,
            payload=payload,
            correlation_id=correlation_id,
            integrity_hash=ih,
            ingested_at=ingested,
            retention_class=retention_class,
        )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.SOURCE == "" and not getattr(cls, "_ABSTRACT", False):
            # Allow intermediate ABC subclasses to skip the check by
            # setting _ABSTRACT = True. Concrete adapters MUST set
            # SOURCE.
            raise AdapterError(
                f"Adapter subclass {cls.__name__} must set SOURCE class "
                f"attribute (the telemetry source identifier)"
            )
