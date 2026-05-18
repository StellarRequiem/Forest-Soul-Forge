"""B389 (ADR-0065 T1) — Sigma-subset parser + DetectionRule.

Tests cover:
  - Parser happy path + each required-field rejection.
  - Reject of unsupported Sigma features (timeframe, modifiers).
  - DetectionRule.evaluate() over varied selection+condition shapes.
  - rule_version stable under whitespace/key-order change.
  - parse_rules_from_dir aggregates failures rather than raising.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.security.detection import (
    DetectionRule,
    DetectionRuleError,
    parse_rule,
    parse_rules_from_dir,
)
from forest_soul_forge.security.detection.events import rule_version_hash


# ---- Happy-path parser ---------------------------------------------------

def _example_rule_body() -> str:
    return """
id: suspicious_shell_spawn
title: Suspicious shell spawn
description: Detect bash spawning from unusual parent.
level: high
tags:
  - attack.T1059.004
logsource:
  source: macos_unified_log
  event_type: process_spawn
detection:
  selection:
    process.image: /bin/bash
    process.parent.name: launchd
  condition: selection
"""


def test_parse_happy_path():
    rule = parse_rule(_example_rule_body())
    assert isinstance(rule, DetectionRule)
    assert rule.rule_id == "suspicious_shell_spawn"
    assert rule.level == "high"
    assert rule.tags == ("attack.T1059.004",)
    assert rule.logsource_source == "macos_unified_log"
    assert rule.logsource_event_type == "process_spawn"
    assert rule.condition == "selection"
    assert "selection" in rule.selections
    assert rule.selections["selection"] == {
        "process.image": "/bin/bash",
        "process.parent.name": "launchd",
    }


def test_parse_rejects_missing_id():
    body = """
title: x
level: high
tags: [attack.T1059]
detection:
  selection: {a: b}
  condition: selection
"""
    with pytest.raises(DetectionRuleError, match="missing 'id'"):
        parse_rule(body)


def test_parse_rejects_missing_tags():
    body = """
id: x
title: x
level: high
detection:
  selection: {a: b}
  condition: selection
"""
    with pytest.raises(DetectionRuleError, match="'tags' is required"):
        parse_rule(body)


def test_parse_rejects_empty_tags_list():
    body = """
id: x
title: x
level: high
tags: []
detection:
  selection: {a: b}
  condition: selection
"""
    with pytest.raises(DetectionRuleError, match="'tags' is required"):
        parse_rule(body)


def test_parse_rejects_unsupported_modifier():
    body = """
id: x
title: x
level: high
tags: [attack.unknown]
detection:
  selection:
    field|contains: foo
  condition: selection
"""
    with pytest.raises(DetectionRuleError, match="modifiers are unsupported"):
        parse_rule(body)


def test_parse_rejects_timeframe():
    body = """
id: x
title: x
level: high
tags: [attack.unknown]
detection:
  selection: {a: b}
  condition: selection
  timeframe: 60s
"""
    with pytest.raises(DetectionRuleError, match="'timeframe' is unsupported"):
        parse_rule(body)


def test_parse_rejects_missing_condition():
    body = """
id: x
title: x
level: high
tags: [attack.unknown]
detection:
  selection: {a: b}
"""
    with pytest.raises(DetectionRuleError, match="condition is required"):
        parse_rule(body)


def test_parse_rejects_no_selections():
    body = """
id: x
title: x
level: high
tags: [attack.unknown]
detection:
  condition: selection
"""
    with pytest.raises(DetectionRuleError, match="at least one selection"):
        parse_rule(body)


def test_parse_rejects_invalid_level():
    body = """
id: x
title: x
level: extreme
tags: [attack.unknown]
detection:
  selection: {a: b}
  condition: selection
"""
    with pytest.raises(DetectionRuleError, match="level must be in"):
        parse_rule(body)


# ---- Evaluate() ----------------------------------------------------------

def test_evaluate_matches_simple_selection():
    rule = parse_rule(_example_rule_body())
    match = rule.evaluate(
        event_id="ev1",
        event_source="macos_unified_log",
        event_type="process_spawn",
        event_payload={
            "process": {"image": "/bin/bash", "parent": {"name": "launchd"}},
        },
    )
    assert match is not None
    assert match.rule_id == "suspicious_shell_spawn"
    assert match.event_id == "ev1"
    assert match.technique == "attack.T1059.004"
    assert match.level == "high"
    assert match.matched_selections == ("selection",)


def test_evaluate_no_match_on_different_image():
    rule = parse_rule(_example_rule_body())
    match = rule.evaluate(
        event_id="ev2",
        event_source="macos_unified_log",
        event_type="process_spawn",
        event_payload={
            "process": {"image": "/bin/zsh", "parent": {"name": "launchd"}},
        },
    )
    assert match is None


def test_evaluate_logsource_mismatch_skips_eval():
    rule = parse_rule(_example_rule_body())
    match = rule.evaluate(
        event_id="ev3",
        event_source="some_other_source",
        event_type="process_spawn",
        event_payload={"process": {"image": "/bin/bash"}},
    )
    assert match is None


def test_evaluate_with_or_condition():
    body = """
id: shell_or_python
title: shell or python spawn
level: medium
tags: [attack.T1059]
detection:
  shell:
    process.image: /bin/bash
  python:
    process.image: /usr/bin/python3
  condition: shell or python
"""
    rule = parse_rule(body)
    # Shell match.
    m1 = rule.evaluate(
        event_id="e1", event_source="", event_type="",
        event_payload={"process": {"image": "/bin/bash"}},
    )
    assert m1 is not None
    assert "shell" in m1.matched_selections
    # Python match.
    m2 = rule.evaluate(
        event_id="e2", event_source="", event_type="",
        event_payload={"process": {"image": "/usr/bin/python3"}},
    )
    assert m2 is not None
    assert "python" in m2.matched_selections
    # Neither.
    m3 = rule.evaluate(
        event_id="e3", event_source="", event_type="",
        event_payload={"process": {"image": "/usr/bin/perl"}},
    )
    assert m3 is None


def test_evaluate_with_and_not_condition():
    body = """
id: bash_not_login
title: bash but not from login shell
level: medium
tags: [attack.unknown]
detection:
  bash:
    process.image: /bin/bash
  login:
    process.parent.name: login
  condition: bash and not login
"""
    rule = parse_rule(body)
    # bash + non-login parent -> match.
    m1 = rule.evaluate(
        event_id="e1", event_source="", event_type="",
        event_payload={"process": {"image": "/bin/bash", "parent": {"name": "launchd"}}},
    )
    assert m1 is not None
    # bash + login parent -> no match (excluded by 'not login').
    m2 = rule.evaluate(
        event_id="e2", event_source="", event_type="",
        event_payload={"process": {"image": "/bin/bash", "parent": {"name": "login"}}},
    )
    assert m2 is None


def test_evaluate_rejects_unknown_selection_in_condition():
    body = """
id: bad_cond
title: bad
level: low
tags: [attack.unknown]
detection:
  s1:
    a: b
  condition: s1 and s2
"""
    rule = parse_rule(body)
    with pytest.raises(DetectionRuleError, match="unknown selection"):
        rule.evaluate(
            event_id="e1", event_source="", event_type="",
            event_payload={"a": "b"},
        )


def test_condition_rejects_unsupported_syntax():
    body = """
id: bad_cond
title: bad
level: low
tags: [attack.unknown]
detection:
  s1: {a: b}
  condition: "s1 == s1"
"""
    rule = parse_rule(body)
    with pytest.raises(DetectionRuleError, match="unsupported syntax"):
        rule.evaluate(
            event_id="e1", event_source="", event_type="",
            event_payload={"a": "b"},
        )


# ---- rule_version stability ----------------------------------------------

def test_rule_version_stable_under_whitespace_and_key_order():
    body_a = """
id: x
title: x
level: high
tags: [attack.T1059]
detection:
  selection: {a: b}
  condition: selection
"""
    body_b = """
detection:
  condition: selection
  selection:
    a: b
tags:
  - attack.T1059
level: high
title: x
id: x
"""
    rule_a = parse_rule(body_a)
    rule_b = parse_rule(body_b)
    assert rule_a.rule_version == rule_b.rule_version


def test_rule_version_changes_when_selection_value_changes():
    body_a = """
id: x
title: x
level: high
tags: [attack.T1059]
detection:
  selection: {a: b}
  condition: selection
"""
    body_b = body_a.replace("b", "c")  # changes the value
    assert parse_rule(body_a).rule_version != parse_rule(body_b).rule_version


def test_rule_version_hash_helper_matches_sha256():
    """Sanity — verify the hash is what we claim it is."""
    import hashlib
    body = "abc"
    assert rule_version_hash(body) == hashlib.sha256(b"abc").hexdigest()


# ---- parse_rules_from_dir ------------------------------------------------

def test_parse_rules_from_dir_loads_and_reports_failures(tmp_path):
    good = tmp_path / "good.yml"
    good.write_text(_example_rule_body(), encoding="utf-8")

    # Missing tags.
    bad = tmp_path / "bad.yml"
    bad.write_text("""
id: missing_tags
title: nope
level: low
detection:
  s: {a: b}
  condition: s
""", encoding="utf-8")

    parsed, failed = parse_rules_from_dir(tmp_path)
    assert len(parsed) == 1
    assert parsed[0].rule_id == "suspicious_shell_spawn"
    assert len(failed) == 1
    assert failed[0][0].name == "bad.yml"
    assert "tags" in str(failed[0][1])


def test_parse_rules_from_dir_missing_dir_is_empty():
    parsed, failed = parse_rules_from_dir(Path("/nonexistent/path"))
    assert parsed == []
    assert failed == []
