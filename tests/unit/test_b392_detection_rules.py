"""B392 (ADR-0065 T4/T5) — starter rule library + harness contract.

T4 extended section-01 (validate config/detection_rules/*.yml) and
section-08 (detection_fired event shape). T5 shipped the operator
runbook + the 8-rule starter library. This file covers the parts
of T4/T5 that have a Python surface:

  - the starter library parses clean (the exact check section-01
    runs — a Python mirror so a bad rule fails CI, not just the
    bash harness)
  - every starter rule satisfies the ADR-0065 invariants (≥1
    ATT&CK tag, filename == id)
  - the DetectionEngine loads the library and is ready()
  - a scan over the library emits a detection_fired entry carrying
    the ADR-0065 D6 shape that section-08 asserts on disk
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from forest_soul_forge.security.detection import (
    DetectionEngine,
    parse_rules_from_dir,
)
from forest_soul_forge.security.telemetry.events import (
    TelemetryEvent,
    compute_integrity_hash,
)

REPO = Path(__file__).resolve().parents[2]
RULES_DIR = REPO / "config" / "detection_rules"

# ADR-0065 D6 — the event_data keys section-08 asserts on every
# detection_fired chain entry.
D6_REQUIRED_KEYS = {
    "rule_id", "rule_version", "batch_id",
    "technique", "severity", "matched_event_ids",
}


def _make_event(
    *, source: str, event_type: str, payload: dict[str, Any],
    severity: str = "info", suffix: str = "01",
) -> TelemetryEvent:
    ts = f"2026-05-22T09:00:{suffix}Z"
    ih = compute_integrity_hash(
        timestamp=ts, source=source, event_type=event_type,
        severity=severity, payload=payload, correlation_id=None,
        retention_class="standard",
    )
    return TelemetryEvent(
        event_id=ih[:16], timestamp=ts, source=source,
        event_type=event_type, severity=severity, payload=payload,
        correlation_id=None, integrity_hash=ih,
        ingested_at=ts, retention_class="standard",
    )


# ---- library integrity ---------------------------------------------------


def test_starter_library_parses_clean():
    """The exact parse section-01 runs. ADR-0065 D7: one bad rule
    halts the whole engine, so the library must be 100% clean."""
    assert RULES_DIR.exists(), "config/detection_rules/ must exist"
    parsed, failed = parse_rules_from_dir(RULES_DIR)
    assert not failed, (
        "starter rule(s) failed to parse: "
        + "; ".join(f"{p.name or '(dup)'}: {e}" for p, e in failed)
    )
    # ADR-0065 T5 spec — 5-10 starter rules.
    assert 5 <= len(parsed) <= 10, f"expected 5-10 starter rules, got {len(parsed)}"


def test_every_rule_has_attack_tag():
    """ADR-0065 D3 — ATT&CK tagging is mandatory; the substrate
    already enforces ≥1 tag, this pins the attack.* convention."""
    parsed, _ = parse_rules_from_dir(RULES_DIR)
    for rule in parsed:
        assert rule.tags, f"{rule.rule_id}: no tags"
        assert any(t.startswith("attack.") for t in rule.tags), (
            f"{rule.rule_id}: no attack.* tag (got {list(rule.tags)})"
        )


def test_rule_filename_matches_id():
    """ADR-0065 D2 — one rule per file, filename is the rule id."""
    for path in sorted(RULES_DIR.glob("*.yml")):
        parsed, failed = parse_rules_from_dir(RULES_DIR)
        rule = next((r for r in parsed if r.rule_id == path.stem), None)
        assert rule is not None, (
            f"{path.name}: no rule with id == filename stem {path.stem!r}"
        )


def test_engine_loads_library_and_is_ready():
    engine = DetectionEngine(rules_dir=RULES_DIR)
    assert engine.ready(), f"engine not ready: {engine.load_errors}"
    assert len(engine.rules) >= 5


# ---- scan smoke — exercises the D6 detection_fired shape -----------------


class _StubChain:
    """Minimal append-only chain stub — mirrors test_b390."""

    def __init__(self) -> None:
        self.appended: list[Any] = []
        self._seq = 5000

    def append(self, event_type, event_data, agent_dna=None):
        self._seq += 1
        entry = type("Entry", (), {
            "seq": self._seq, "event_type": event_type,
            "event_data": event_data, "agent_dna": agent_dna,
        })()
        self.appended.append(entry)
        return entry


def test_xprotect_rule_fires_with_d6_shape():
    """The live xprotect rule fires on a matching macos_unified_log
    event and the emitted detection_fired carries every ADR-0065 D6
    key section-08 asserts."""
    engine = DetectionEngine(rules_dir=RULES_DIR)
    chain = _StubChain()
    ev = _make_event(
        source="macos_unified_log", event_type="log_line",
        payload={"subsystem": "com.apple.xprotect",
                 "message_type": "Fault", "message": "remediation"},
    )
    result = engine.scan("batch-xp", [ev], audit_chain=chain)

    assert "xprotect_malware_flagged" in result.matches_by_rule
    fired = [e for e in chain.appended if e.event_type == "detection_fired"]
    assert len(fired) == 1
    ed = fired[0].event_data
    assert D6_REQUIRED_KEYS.issubset(ed.keys()), (
        f"detection_fired missing D6 keys: {D6_REQUIRED_KEYS - set(ed)}"
    )
    assert ed["rule_id"] == "xprotect_malware_flagged"
    assert ed["batch_id"] == "batch-xp"
    assert ed["technique"] == "attack.T1204.002"
    assert ed["severity"] == "high"
    assert ed["matched_event_ids"] == [ev.event_id]


def test_non_matching_event_fires_nothing():
    """An xprotect event with the wrong message_type must not trip
    the rule — the AND condition needs both selections."""
    engine = DetectionEngine(rules_dir=RULES_DIR)
    chain = _StubChain()
    ev = _make_event(
        source="macos_unified_log", event_type="log_line",
        payload={"subsystem": "com.apple.xprotect",
                 "message_type": "Default"},
    )
    result = engine.scan("batch-quiet", [ev], audit_chain=chain)
    assert "xprotect_malware_flagged" not in result.matches_by_rule
    assert [e for e in chain.appended if e.event_type == "detection_fired"] == []


def test_template_rule_inert_without_its_event_type():
    """A process_spawn template rule must not fire on a log_line
    event — applies_to() short-circuits on the logsource mismatch."""
    engine = DetectionEngine(rules_dir=RULES_DIR)
    chain = _StubChain()
    ev = _make_event(
        source="macos_unified_log", event_type="log_line",
        payload={"process": {"image": "/usr/sbin/spctl"}},
    )
    result = engine.scan("batch-mismatch", [ev], audit_chain=chain)
    assert "gatekeeper_disable_attempt" not in result.matches_by_rule
