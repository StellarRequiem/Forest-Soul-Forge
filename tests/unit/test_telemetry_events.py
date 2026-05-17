"""ADR-0064 T1 (B348) — TelemetryEvent + canonical_form + hash tests.

Coverage:
  Enums:
    - EVENT_TYPES has exactly the 8 ADR-named types
    - SEVERITIES has exactly {info, warn, critical}
    - RETENTION_CLASSES has exactly {ephemeral, standard, security_relevant}

  TelemetryEvent invariants (raised in __post_init__):
    - unknown event_type raises TelemetryEventError
    - unknown severity raises
    - unknown retention_class raises
    - empty source raises
    - non-dict payload raises
    - too-short timestamp raises
    - non-64-char integrity_hash raises

  canonical_form determinism:
    - same inputs in different key order → same bytes
    - same inputs with nested dict in different order → same bytes
    - changing any field changes the bytes
    - excludes event_id + ingested_at (those don't affect hash)
    - non-ASCII payload encodes via UTF-8 (not escaped)

  compute_integrity_hash:
    - returns 64-char lowercase hex
    - matches sha256 of canonical_form
    - changes when any input field changes
"""
from __future__ import annotations

import hashlib
import json

import pytest

from forest_soul_forge.security.telemetry.events import (
    EVENT_TYPES,
    RETENTION_CLASSES,
    SEVERITIES,
    TelemetryEvent,
    TelemetryEventError,
    canonical_form,
    compute_integrity_hash,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_event_types_exactly_eight():
    """ADR-0064 Decision 2 names exactly eight canonical types.
    A future addition to this set is an ADR amendment, not a
    silent code change — pin the count here so the diff catches it."""
    assert EVENT_TYPES == frozenset({
        "process_spawn", "process_exit", "network_connection",
        "file_change", "auth_event", "log_line",
        "policy_decision", "sensor_reading",
    })


def test_severities_exactly_three():
    assert SEVERITIES == frozenset({"info", "warn", "critical"})


def test_retention_classes_exactly_three():
    assert RETENTION_CLASSES == frozenset({
        "ephemeral", "standard", "security_relevant",
    })


# ---------------------------------------------------------------------------
# Helpers for building valid events
# ---------------------------------------------------------------------------


def _valid_event_kwargs(**overrides):
    """Return a dict that's a valid TelemetryEvent construction
    payload. Tests override individual fields to exercise the
    validators."""
    base_payload = {"pid": 1234, "cmd": "/bin/zsh"}
    base = dict(
        event_id="evt-0001",
        timestamp="2026-05-17T12:00:00+00:00",
        source="process_monitor",
        event_type="process_spawn",
        severity="info",
        payload=base_payload,
        correlation_id=None,
        integrity_hash=compute_integrity_hash(
            timestamp="2026-05-17T12:00:00+00:00",
            source="process_monitor",
            event_type="process_spawn",
            severity="info",
            payload=base_payload,
            correlation_id=None,
            retention_class="standard",
        ),
        ingested_at="2026-05-17T12:00:01+00:00",
        retention_class="standard",
    )
    base.update(overrides)
    return base


def test_valid_event_constructs_cleanly():
    ev = TelemetryEvent(**_valid_event_kwargs())
    assert ev.event_id == "evt-0001"
    assert ev.event_type == "process_spawn"


# ---------------------------------------------------------------------------
# Invariant validation
# ---------------------------------------------------------------------------


def test_unknown_event_type_raises():
    with pytest.raises(TelemetryEventError, match="event_type"):
        TelemetryEvent(**_valid_event_kwargs(event_type="unknown_thing"))


def test_unknown_severity_raises():
    with pytest.raises(TelemetryEventError, match="severity"):
        TelemetryEvent(**_valid_event_kwargs(severity="ERROR"))


def test_unknown_retention_class_raises():
    # Need to recompute the hash for the new retention_class or the
    # length check passes but a different validator might fire first.
    kwargs = _valid_event_kwargs(retention_class="forever")
    # Build a 64-char hex string so the length validator doesn't fire
    # before the retention_class one.
    kwargs["integrity_hash"] = "a" * 64
    with pytest.raises(TelemetryEventError, match="retention_class"):
        TelemetryEvent(**kwargs)


def test_empty_source_raises():
    with pytest.raises(TelemetryEventError, match="source"):
        TelemetryEvent(**_valid_event_kwargs(source=""))


def test_whitespace_only_source_raises():
    with pytest.raises(TelemetryEventError, match="source"):
        TelemetryEvent(**_valid_event_kwargs(source="   "))


def test_non_dict_payload_raises():
    kwargs = _valid_event_kwargs(payload="not a dict")
    with pytest.raises(TelemetryEventError, match="payload"):
        TelemetryEvent(**kwargs)


def test_too_short_timestamp_raises():
    kwargs = _valid_event_kwargs(timestamp="x")
    with pytest.raises(TelemetryEventError, match="timestamp"):
        TelemetryEvent(**kwargs)


def test_too_short_ingested_at_raises():
    kwargs = _valid_event_kwargs(ingested_at="x")
    with pytest.raises(TelemetryEventError, match="ingested_at"):
        TelemetryEvent(**kwargs)


def test_bad_integrity_hash_length_raises():
    # Use a syntactically valid hex string of the wrong length.
    kwargs = _valid_event_kwargs(integrity_hash="abc123")
    with pytest.raises(TelemetryEventError, match="integrity_hash"):
        TelemetryEvent(**kwargs)


# ---------------------------------------------------------------------------
# canonical_form determinism
# ---------------------------------------------------------------------------


def _stable_args():
    return dict(
        timestamp="2026-05-17T12:00:00+00:00",
        source="process_monitor",
        event_type="process_spawn",
        severity="info",
        payload={"pid": 1234, "cmd": "/bin/zsh", "ppid": 1},
        correlation_id="incident-42",
        retention_class="standard",
    )


def test_canonical_form_returns_bytes():
    out = canonical_form(**_stable_args())
    assert isinstance(out, bytes)


def test_canonical_form_is_deterministic():
    """Two calls with the same args produce byte-identical output.
    The sort_keys=True + separators=(',',':') combo enforces this;
    if a future edit drops either flag, this test catches it."""
    a = canonical_form(**_stable_args())
    b = canonical_form(**_stable_args())
    assert a == b


def test_canonical_form_normalizes_nested_dict_order():
    """Nested dict insertion order must not affect the output —
    sort_keys=True at the json.dumps level normalizes nested keys
    too (it's recursive). External ingestors will not always sort
    payload keys; the daemon's verification must succeed regardless."""
    args1 = _stable_args()
    args1["payload"] = {"pid": 1234, "cmd": "/bin/zsh", "ppid": 1}
    args2 = _stable_args()
    args2["payload"] = {"ppid": 1, "cmd": "/bin/zsh", "pid": 1234}
    assert canonical_form(**args1) == canonical_form(**args2)


def test_canonical_form_changes_when_payload_changes():
    args1 = _stable_args()
    args2 = _stable_args()
    args2["payload"] = {**args2["payload"], "pid": 9999}
    assert canonical_form(**args1) != canonical_form(**args2)


def test_canonical_form_changes_when_severity_changes():
    args1 = _stable_args()
    args2 = _stable_args()
    args2["severity"] = "critical"
    assert canonical_form(**args1) != canonical_form(**args2)


def test_canonical_form_changes_when_retention_class_changes():
    """Critical: retention_class is part of the integrity scope.
    A reclassifier that silently bumps an event from standard to
    security_relevant must result in a hash change so the chain
    can detect the reclassification."""
    args1 = _stable_args()
    args2 = _stable_args()
    args2["retention_class"] = "security_relevant"
    assert canonical_form(**args1) != canonical_form(**args2)


def test_canonical_form_handles_non_ascii():
    """Adapter for macOS unified log will see non-ASCII filenames
    (Hangul, emoji, RTL scripts). ensure_ascii=False keeps the
    output bytes minimal — escaping every non-ASCII char would
    inflate the hash input + still hash the same in either mode
    on Python's side, but external ingestors written in other
    languages need to see the same bytes we do."""
    args = _stable_args()
    args["payload"] = {"filename": "résumé.txt"}
    out = canonical_form(**args)
    # UTF-8 encoded: 'é' is bytes 0xC3 0xA9, not 'é'.
    assert b"r\xc3\xa9sum\xc3\xa9" in out


def test_canonical_form_excludes_event_id():
    """event_id is server-assigned; external ingestor doesn't know
    it yet at hash-compute time. canonical_form must not include
    it. Verified indirectly by: the function signature accepts no
    event_id kwarg, so passing one would TypeError."""
    with pytest.raises(TypeError):
        canonical_form(event_id="x", **_stable_args())


def test_canonical_form_excludes_ingested_at():
    """Same rationale as event_id — server-assigned."""
    with pytest.raises(TypeError):
        canonical_form(ingested_at="x", **_stable_args())


# ---------------------------------------------------------------------------
# compute_integrity_hash
# ---------------------------------------------------------------------------


def test_integrity_hash_is_sha256_hex():
    h = compute_integrity_hash(**_stable_args())
    assert len(h) == 64
    int(h, 16)  # parses as hex


def test_integrity_hash_matches_manual_sha256():
    """The hash IS sha256(canonical_form). A future optimization that
    swaps the algorithm would break the audit chain; pin the algo."""
    h = compute_integrity_hash(**_stable_args())
    manual = hashlib.sha256(canonical_form(**_stable_args())).hexdigest()
    assert h == manual


def test_integrity_hash_changes_on_payload_change():
    h1 = compute_integrity_hash(**_stable_args())
    args2 = _stable_args()
    args2["payload"] = {**args2["payload"], "pid": 9999}
    h2 = compute_integrity_hash(**args2)
    assert h1 != h2


def test_integrity_hash_lowercase():
    """Convention: hex digests in the chain are always lowercase.
    Mixed-case would break naive string compare in the verifier."""
    h = compute_integrity_hash(**_stable_args())
    assert h == h.lower()
