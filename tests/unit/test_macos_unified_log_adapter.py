"""ADR-0064 T2 (B349) — MacosUnifiedLogAdapter parser tests.

Coverage:
  Construction:
    - default predicate is non-empty
    - empty predicate raises
    - SOURCE class attr == "macos_unified_log"
    - command() returns the expected argv shape

  parse():
    - happy path: minimal valid ndjson line → TelemetryEvent
    - empty line / whitespace → None
    - non-JSON line → None (parse MUST NOT raise per contract)
    - JSON not a dict → None
    - missing timestamp → None
    - severity mapping: Default/Info/Debug → info; Error → warn;
      Fault → critical; unknown → info
    - event_type mapping: securityd/authd/opendirectoryd → auth_event;
      xprotect → policy_decision; networking.* → log_line; other → log_line
    - correlation_id: activity_id present → string; missing/0 → None
    - payload contains the subsystem + category + process + pid

  retention_override:
    - auth_event → security_relevant
    - policy_decision → security_relevant
    - log_line → None (defer to classifier)
"""
from __future__ import annotations

import json

import pytest

from forest_soul_forge.security.telemetry.adapters.macos_unified_log import (
    DEFAULT_PREDICATE,
    MacosUnifiedLogAdapter,
)


# ---------------------------------------------------------------------------
# Construction + class shape
# ---------------------------------------------------------------------------


def test_source_attr():
    assert MacosUnifiedLogAdapter.SOURCE == "macos_unified_log"


def test_default_predicate_non_empty():
    assert isinstance(DEFAULT_PREDICATE, str) and DEFAULT_PREDICATE.strip()


def test_construct_with_default_predicate():
    a = MacosUnifiedLogAdapter()
    assert a.predicate == DEFAULT_PREDICATE


def test_construct_with_custom_predicate():
    a = MacosUnifiedLogAdapter(predicate='subsystem == "test"')
    assert a.predicate == 'subsystem == "test"'


def test_empty_predicate_raises():
    with pytest.raises(ValueError, match="predicate"):
        MacosUnifiedLogAdapter(predicate="")


def test_whitespace_only_predicate_raises():
    with pytest.raises(ValueError, match="predicate"):
        MacosUnifiedLogAdapter(predicate="   ")


def test_command_argv_shape():
    """The first elements of argv must be `log stream --style ndjson`
    so the parser can rely on ndjson framing. Variation in this
    prefix is a regression."""
    a = MacosUnifiedLogAdapter(predicate="subsystem == \"x\"")
    cmd = a.command()
    assert cmd[0] == "log"
    assert cmd[1] == "stream"
    # --style ndjson is the documented machine-readable mode.
    assert "--style" in cmd and "ndjson" in cmd
    # Predicate landed correctly.
    assert "--predicate" in cmd
    assert "subsystem == \"x\"" in cmd


# ---------------------------------------------------------------------------
# Helper to build canned ndjson lines
# ---------------------------------------------------------------------------


def _line(**overrides):
    base = {
        "timestamp": "2026-05-17T12:34:56-07:00",
        "subsystem": "com.apple.securityd",
        "category": "auth",
        "messageType": "Default",
        "eventMessage": "test message",
        "processImagePath": "/usr/sbin/securityd",
        "processID": 200,
        "threadID": 1234,
        "activityIdentifier": 555,
    }
    base.update(overrides)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# parse() — happy + edge cases
# ---------------------------------------------------------------------------


def test_parse_happy_path_minimal():
    a = MacosUnifiedLogAdapter()
    ev = a.parse(_line())
    assert ev is not None
    assert ev.source == "macos_unified_log"
    assert ev.timestamp == "2026-05-17T12:34:56-07:00"
    assert ev.event_type == "auth_event"   # securityd subsystem
    assert ev.severity == "info"           # messageType Default
    assert ev.payload["subsystem"] == "com.apple.securityd"
    assert ev.payload["category"] == "auth"
    assert ev.payload["process"] == "/usr/sbin/securityd"
    assert ev.payload["pid"] == 200
    # activity_id present + non-zero → correlation_id set.
    assert ev.correlation_id == "555"


def test_parse_empty_line_returns_none():
    a = MacosUnifiedLogAdapter()
    assert a.parse("") is None
    assert a.parse("   ") is None


def test_parse_non_json_line_returns_none():
    """`log stream` emits a meta-line like "Filtering..." before
    the first event. parse must drop these silently — not raise."""
    a = MacosUnifiedLogAdapter()
    assert a.parse("Filtering the log data using ...") is None
    assert a.parse("garbage") is None


def test_parse_malformed_json_returns_none():
    """parse MUST NOT raise on bad JSON — adapter contract."""
    a = MacosUnifiedLogAdapter()
    assert a.parse('{"incomplete":') is None


def test_parse_json_not_dict_returns_none():
    a = MacosUnifiedLogAdapter()
    assert a.parse("[1, 2, 3]") is None
    assert a.parse('"a string"') is None


def test_parse_missing_timestamp_returns_none():
    """Without a timestamp the event is unusable downstream
    (no ordering, no retention). Drop it."""
    a = MacosUnifiedLogAdapter()
    line = json.dumps({
        "subsystem": "com.apple.securityd",
        "messageType": "Default",
        "eventMessage": "no ts",
    })
    assert a.parse(line) is None


@pytest.mark.parametrize("message_type,expected", [
    ("Default",  "info"),
    ("Info",     "info"),
    ("Debug",    "info"),
    ("Error",    "warn"),
    ("Fault",    "critical"),
    ("Unknown",  "info"),   # fall-through default
    ("",         "info"),
])
def test_parse_severity_mapping(message_type, expected):
    a = MacosUnifiedLogAdapter()
    ev = a.parse(_line(messageType=message_type))
    assert ev is not None
    assert ev.severity == expected


@pytest.mark.parametrize("subsystem,expected_type", [
    ("com.apple.securityd",      "auth_event"),
    ("com.apple.authd",          "auth_event"),
    ("com.apple.opendirectoryd", "auth_event"),
    ("com.apple.xprotect",       "policy_decision"),
    ("com.apple.networking.tcp", "log_line"),
    ("com.apple.networking",     "log_line"),
    ("com.apple.kernel",         "log_line"),
    ("",                          "log_line"),
])
def test_parse_event_type_mapping(subsystem, expected_type):
    a = MacosUnifiedLogAdapter()
    ev = a.parse(_line(subsystem=subsystem))
    assert ev is not None
    assert ev.event_type == expected_type


def test_parse_correlation_id_present():
    a = MacosUnifiedLogAdapter()
    ev = a.parse(_line(activityIdentifier=42))
    assert ev is not None
    assert ev.correlation_id == "42"


@pytest.mark.parametrize("activity_value", [None, 0, "0"])
def test_parse_correlation_id_absent_when_zero(activity_value):
    """activity_id of 0 / "0" / None means 'no activity' in the
    unified log; we don't store that as a correlation_id because it
    would lump every no-activity event together as if they were
    related."""
    a = MacosUnifiedLogAdapter()
    ev = a.parse(_line(activityIdentifier=activity_value))
    assert ev is not None
    assert ev.correlation_id is None


def test_parse_payload_includes_expected_fields():
    a = MacosUnifiedLogAdapter()
    ev = a.parse(_line(
        subsystem="com.apple.securityd",
        category="keychain",
        messageType="Error",
        eventMessage="something failed",
        processImagePath="/usr/sbin/securityd",
        processID=999,
        threadID=7777,
    ))
    assert ev is not None
    p = ev.payload
    assert p["subsystem"] == "com.apple.securityd"
    assert p["category"] == "keychain"
    assert p["message_type"] == "Error"
    assert p["message"] == "something failed"
    assert p["process"] == "/usr/sbin/securityd"
    assert p["pid"] == 999
    assert p["thread_id"] == 7777


# ---------------------------------------------------------------------------
# retention_override
# ---------------------------------------------------------------------------


def test_retention_override_auth_event():
    a = MacosUnifiedLogAdapter()
    ev = a.parse(_line(subsystem="com.apple.securityd"))
    assert a.retention_override(ev) == "security_relevant"


def test_retention_override_policy_decision():
    a = MacosUnifiedLogAdapter()
    ev = a.parse(_line(subsystem="com.apple.xprotect"))
    assert a.retention_override(ev) == "security_relevant"


def test_retention_override_log_line_defers():
    """log_line falls through to the central classifier
    (which sees info-level + log_line → ephemeral)."""
    a = MacosUnifiedLogAdapter()
    ev = a.parse(_line(subsystem="com.apple.kernel"))
    assert a.retention_override(ev) is None
