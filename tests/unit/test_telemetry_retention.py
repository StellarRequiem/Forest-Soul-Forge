"""ADR-0064 T1 (B348) — retention policy + classifier + sweep tests.

Coverage:
  Defaults:
    - DEFAULT_RETENTION_TTLS exactly {ephemeral 7, standard 90, security_relevant 365}
    - RetentionPolicy.default() round-trips the defaults
  cutoff_for:
    - returns now - ttl_days
    - unknown class raises KeyError (loud fail, not silent zero)
  classify_retention:
    - severity=critical → security_relevant regardless of type
    - auth_event → security_relevant regardless of severity
    - policy_decision → security_relevant
    - process_spawn + info → ephemeral
    - log_line + info → ephemeral
    - process_spawn + warn → standard (severity bumps out of ephemeral)
    - log_line + critical → security_relevant (Rule 1 wins over Rule 4)
    - default for unhandled types → standard
  retention_sweep:
    - deletes events older than TTL per class
    - returns {class: count_deleted}
    - leaves fresh events alone
    - idempotent (second sweep with same now returns 0s)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from forest_soul_forge.security.telemetry.events import (
    TelemetryEvent,
    compute_integrity_hash,
)
from forest_soul_forge.security.telemetry.retention import (
    DEFAULT_RETENTION_TTLS,
    RetentionPolicy,
    classify_retention,
)
from forest_soul_forge.security.telemetry.store import (
    SqliteTelemetryStore,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_retention_ttls_match_adr():
    """ADR-0064 Decision 4 names exactly these three TTLs.
    Changing any value is an ADR amendment, not a code change."""
    assert DEFAULT_RETENTION_TTLS == {
        "ephemeral": 7,
        "standard": 90,
        "security_relevant": 365,
    }


def test_policy_default_round_trips_ttls():
    p = RetentionPolicy.default()
    assert p.ttls == DEFAULT_RETENTION_TTLS
    # Frozen — mutating the returned dict mustn't change the default.
    # (We don't enforce frozen here; instead, dict(...) gives a copy.)
    p.ttls["ephemeral"] = 999
    p2 = RetentionPolicy.default()
    assert p2.ttls["ephemeral"] == 7


# ---------------------------------------------------------------------------
# cutoff_for
# ---------------------------------------------------------------------------


def test_cutoff_for_standard():
    p = RetentionPolicy.default()
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = p.cutoff_for("standard", now=now)
    assert cutoff == now - timedelta(days=90)


def test_cutoff_for_ephemeral():
    p = RetentionPolicy.default()
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = p.cutoff_for("ephemeral", now=now)
    assert cutoff == now - timedelta(days=7)


def test_cutoff_for_unknown_class_raises():
    """Loud fail — silently treating an unknown class as 0-day would
    delete everything matching it."""
    p = RetentionPolicy.default()
    with pytest.raises(KeyError):
        p.cutoff_for("forever", now=datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# classify_retention
# ---------------------------------------------------------------------------


def test_classify_critical_wins_over_type():
    """Rule 1: severity=critical → security_relevant regardless of type.
    Even an otherwise-ephemeral process_spawn becomes security_relevant
    when something marks it critical."""
    out = classify_retention(
        event_type="process_spawn",  # would be ephemeral if info
        severity="critical",
    )
    assert out == "security_relevant"


def test_classify_auth_event_warn_is_security_relevant():
    """Rule 2: auth_event regardless of severity.
    A warn-level auth event (failed login) is forensically valuable
    even though it didn't trip critical."""
    out = classify_retention(event_type="auth_event", severity="warn")
    assert out == "security_relevant"


def test_classify_auth_event_info_is_security_relevant():
    out = classify_retention(event_type="auth_event", severity="info")
    assert out == "security_relevant"


def test_classify_policy_decision_is_security_relevant():
    """Rule 2: policy_decision. An xprotect quarantine event matters
    forever (within retention budget) regardless of severity."""
    out = classify_retention(event_type="policy_decision", severity="info")
    assert out == "security_relevant"


def test_classify_process_spawn_info_is_ephemeral():
    """Rule 3: high-volume noise on a busy host."""
    out = classify_retention(event_type="process_spawn", severity="info")
    assert out == "ephemeral"


def test_classify_log_line_info_is_ephemeral():
    out = classify_retention(event_type="log_line", severity="info")
    assert out == "ephemeral"


def test_classify_process_spawn_warn_is_standard():
    """Severity bumps the event out of ephemeral but doesn't reach
    security_relevant (no critical, no auth/policy type). Falls into
    Rule 5 default."""
    out = classify_retention(event_type="process_spawn", severity="warn")
    assert out == "standard"


def test_classify_log_line_critical_is_security_relevant():
    """Rule 1 (critical) wins over Rule 4 (log_line + info → ephemeral).
    A critical log line is the canonical 'put this on someone's
    screen' shape."""
    out = classify_retention(event_type="log_line", severity="critical")
    assert out == "security_relevant"


def test_classify_unhandled_type_defaults_to_standard():
    """Rule 5 fallthrough — anything we didn't explicitly name."""
    out = classify_retention(event_type="sensor_reading", severity="info")
    assert out == "standard"


# ---------------------------------------------------------------------------
# retention_sweep integration
# ---------------------------------------------------------------------------


def _make_event(
    *, event_id, age_days, retention_class="standard",
    event_type="process_spawn", severity="info",
):
    """Construct an event with timestamp age_days ago. ingested_at
    is set to the same moment for simplicity."""
    when = datetime.now(timezone.utc) - timedelta(days=age_days)
    ts = when.isoformat()
    payload = {"pid": int(age_days)}
    return TelemetryEvent(
        event_id=event_id, timestamp=ts, source="test",
        event_type=event_type, severity=severity, payload=payload,
        correlation_id=None,
        integrity_hash=compute_integrity_hash(
            timestamp=ts, source="test", event_type=event_type,
            severity=severity, payload=payload,
            correlation_id=None, retention_class=retention_class,
        ),
        ingested_at=ts, retention_class=retention_class,
    )


def test_sweep_deletes_old_standard(tmp_path):
    s = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        # standard TTL = 90 days
        s.ingest(_make_event(event_id="old", age_days=100,
                             retention_class="standard"))
        s.ingest(_make_event(event_id="new", age_days=30,
                             retention_class="standard"))
        deleted = s.retention_sweep(
            policy=RetentionPolicy.default(),
            now=datetime.now(timezone.utc),
        )
        assert deleted["standard"] == 1
        remaining = s.query(limit=100)
        assert [r.event_id for r in remaining] == ["new"]
    finally:
        s.close()


def test_sweep_preserves_security_relevant_within_ttl(tmp_path):
    s = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        # security_relevant TTL = 365 days
        # 100 days old → would delete under standard, kept under security_relevant
        s.ingest(_make_event(
            event_id="auth-100d", age_days=100,
            retention_class="security_relevant",
        ))
        deleted = s.retention_sweep(
            policy=RetentionPolicy.default(),
            now=datetime.now(timezone.utc),
        )
        assert deleted.get("security_relevant", 0) == 0
        assert len(s.query(limit=10)) == 1
    finally:
        s.close()


def test_sweep_deletes_old_ephemeral(tmp_path):
    s = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        # ephemeral TTL = 7 days
        s.ingest(_make_event(event_id="noise-10d", age_days=10,
                             retention_class="ephemeral"))
        s.ingest(_make_event(event_id="noise-3d", age_days=3,
                             retention_class="ephemeral"))
        deleted = s.retention_sweep(
            policy=RetentionPolicy.default(),
            now=datetime.now(timezone.utc),
        )
        assert deleted["ephemeral"] == 1
        remaining = s.query(limit=10)
        assert [r.event_id for r in remaining] == ["noise-3d"]
    finally:
        s.close()


def test_sweep_returns_class_to_count_dict(tmp_path):
    s = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        s.ingest(_make_event(event_id="a", age_days=10,
                             retention_class="ephemeral"))
        s.ingest(_make_event(event_id="b", age_days=100,
                             retention_class="standard"))
        deleted = s.retention_sweep(
            policy=RetentionPolicy.default(),
            now=datetime.now(timezone.utc),
        )
        # All three classes must be in the dict so the caller can
        # emit an audit event with zero-counts for unused classes.
        assert set(deleted.keys()) == {
            "ephemeral", "standard", "security_relevant",
        }
        assert deleted["ephemeral"] == 1
        assert deleted["standard"] == 1
        assert deleted["security_relevant"] == 0
    finally:
        s.close()


def test_sweep_is_idempotent(tmp_path):
    """Second sweep with the same `now` returns all-zeros because the
    eligible rows are already gone. This matters because the caller
    fires ONE audit event per sweep; an idempotent sweep prevents
    double-counting if the cron retries on failure."""
    s = SqliteTelemetryStore(tmp_path / "t.sqlite")
    try:
        s.ingest(_make_event(event_id="old", age_days=100))
        now = datetime.now(timezone.utc)
        first = s.retention_sweep(policy=RetentionPolicy.default(), now=now)
        second = s.retention_sweep(policy=RetentionPolicy.default(), now=now)
        assert first["standard"] == 1
        assert second["standard"] == 0
    finally:
        s.close()
