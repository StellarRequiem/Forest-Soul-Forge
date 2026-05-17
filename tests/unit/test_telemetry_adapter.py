"""ADR-0064 T2 (B349) — Adapter ABC contract tests.

Coverage:
  ABC enforcement:
    - subclass without SOURCE attr raises at class creation
    - intermediate ABC subclass with _ABSTRACT=True is allowed to skip SOURCE
    - subclass that forgets command() can't instantiate
    - subclass that forgets parse() can't instantiate

  make_event helper:
    - returns a valid TelemetryEvent
    - event_id is unique per call
    - integrity_hash is the real sha256 of canonical_form
    - default retention_class is "standard"
    - default ingested_at is auto-populated to a non-empty ISO string

  retention_override default:
    - base class returns None (defer to classify_retention)
"""
from __future__ import annotations

import pytest

from forest_soul_forge.security.telemetry.adapter import (
    Adapter,
    AdapterError,
)
from forest_soul_forge.security.telemetry.events import (
    TelemetryEvent,
    compute_integrity_hash,
)


# ---------------------------------------------------------------------------
# A minimal concrete adapter for the positive-path tests.
# ---------------------------------------------------------------------------


class _DummyAdapter(Adapter):
    SOURCE = "dummy"

    def command(self) -> list[str]:
        return ["echo", "noop"]

    def parse(self, line: str) -> TelemetryEvent | None:
        if not line.strip():
            return None
        return self.make_event(
            timestamp="2026-05-17T12:00:00+00:00",
            event_type="log_line",
            severity="info",
            payload={"raw": line},
        )


# ---------------------------------------------------------------------------
# ABC enforcement
# ---------------------------------------------------------------------------


def test_subclass_without_source_raises():
    """Concrete adapters MUST set SOURCE. A missing attribute is a
    silent-failure trap: the allowlist loader would compare against
    "" and fail with a confusing message."""
    with pytest.raises(AdapterError, match="must set SOURCE"):
        class _BadAdapter(Adapter):  # noqa: D401
            def command(self) -> list[str]:
                return []

            def parse(self, line):
                return None


def test_intermediate_abc_can_skip_source():
    """An intermediate ABC subclass (e.g., a base class for a family
    of related adapters) can opt out of SOURCE by setting
    _ABSTRACT = True. Concrete leaves still have to set SOURCE."""
    class _IntermediateAdapter(Adapter):
        _ABSTRACT = True

        def command(self) -> list[str]:
            return ["base"]

        def parse(self, line):
            return None

    # No error raised — that's the test.
    # Concrete leaf MUST still set SOURCE.
    with pytest.raises(AdapterError, match="must set SOURCE"):
        class _ConcreteLeaf(_IntermediateAdapter):
            _ABSTRACT = False


def test_subclass_without_command_cannot_instantiate():
    class _NoCmd(Adapter):
        SOURCE = "no_cmd"

        def parse(self, line):
            return None

    with pytest.raises(TypeError):
        _NoCmd()


def test_subclass_without_parse_cannot_instantiate():
    class _NoParse(Adapter):
        SOURCE = "no_parse"

        def command(self) -> list[str]:
            return []

    with pytest.raises(TypeError):
        _NoParse()


# ---------------------------------------------------------------------------
# make_event helper
# ---------------------------------------------------------------------------


def test_make_event_returns_valid_event():
    a = _DummyAdapter()
    ev = a.make_event(
        timestamp="2026-05-17T12:00:00+00:00",
        event_type="log_line",
        severity="info",
        payload={"k": "v"},
    )
    assert isinstance(ev, TelemetryEvent)
    assert ev.source == "dummy"
    assert ev.event_type == "log_line"


def test_make_event_event_id_unique():
    """Each make_event call mints a fresh uuid4. If a future edit
    swaps in a deterministic id (for tests), this catches it."""
    a = _DummyAdapter()
    ids = {
        a.make_event(
            timestamp="2026-05-17T12:00:00+00:00",
            event_type="log_line", severity="info", payload={"k": i},
        ).event_id
        for i in range(20)
    }
    assert len(ids) == 20


def test_make_event_integrity_hash_matches_canonical():
    a = _DummyAdapter()
    ev = a.make_event(
        timestamp="2026-05-17T12:00:00+00:00",
        event_type="log_line",
        severity="info",
        payload={"k": "v"},
        correlation_id="abc",
    )
    expected = compute_integrity_hash(
        timestamp="2026-05-17T12:00:00+00:00",
        source="dummy",
        event_type="log_line",
        severity="info",
        payload={"k": "v"},
        correlation_id="abc",
        retention_class="standard",
    )
    assert ev.integrity_hash == expected


def test_make_event_default_retention_class():
    a = _DummyAdapter()
    ev = a.make_event(
        timestamp="2026-05-17T12:00:00+00:00",
        event_type="log_line",
        severity="info",
        payload={},
    )
    assert ev.retention_class == "standard"


def test_make_event_default_ingested_at_populated():
    a = _DummyAdapter()
    ev = a.make_event(
        timestamp="2026-05-17T12:00:00+00:00",
        event_type="log_line",
        severity="info",
        payload={},
    )
    assert isinstance(ev.ingested_at, str) and len(ev.ingested_at) >= 20


# ---------------------------------------------------------------------------
# retention_override default
# ---------------------------------------------------------------------------


def test_retention_override_defaults_to_none():
    """Base class returns None so the ingestor falls through to the
    central classify_retention. Subclasses opt in to overrides."""
    a = _DummyAdapter()
    ev = a.make_event(
        timestamp="2026-05-17T12:00:00+00:00",
        event_type="log_line",
        severity="info",
        payload={},
    )
    assert a.retention_override(ev) is None
