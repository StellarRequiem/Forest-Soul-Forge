"""B390 (ADR-0065 T2) — DetectionEngine + AdapterIngestor wiring.

Tests cover:
  - Engine construction from pre-loaded rules + from rules_dir.
  - reload_from_dir() refuses to swap on any failure; previous
    rule set retained.
  - ready() returns False when load_errors is non-empty.
  - scan() over varied rule+event shapes; single chain event per
    (rule, batch) pair.
  - scan() refuses to run when ready() is False.
  - AdapterIngestor integration: detection_engine.scan called
    after telemetry_batch_ingested anchor.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from forest_soul_forge.security.detection import (
    DetectionEngine,
    DetectionRule,
    parse_rule,
)
from forest_soul_forge.security.telemetry.adapter import Adapter
from forest_soul_forge.security.telemetry.events import (
    TelemetryEvent,
    compute_integrity_hash,
)
from forest_soul_forge.security.telemetry.ingestor import AdapterIngestor
from forest_soul_forge.security.telemetry.store import SqliteTelemetryStore


# ---- Helpers --------------------------------------------------------------


def _make_event(
    *, source="macos_unified_log", event_type="process_spawn",
    payload: dict[str, Any] | None = None, suffix: str = "01",
) -> TelemetryEvent:
    payload = payload or {}
    ts = f"2026-05-18T10:00:{suffix}Z"
    ih = compute_integrity_hash(
        timestamp=ts, source=source, event_type=event_type,
        severity="info", payload=payload, correlation_id=None,
        retention_class="ephemeral",
    )
    return TelemetryEvent(
        event_id=ih[:16], timestamp=ts, source=source,
        event_type=event_type, severity="info", payload=payload,
        correlation_id=None, integrity_hash=ih,
        ingested_at="2026-05-18T10:00:00Z",
        retention_class="ephemeral",
    )


_RULE_SHELL = """
id: suspicious_shell_spawn
title: Suspicious shell spawn
level: high
tags: [attack.T1059.004]
logsource:
  source: macos_unified_log
  event_type: process_spawn
detection:
  selection:
    process.image: /bin/bash
  condition: selection
"""

_RULE_PYTHON = """
id: python_spawn
title: Python spawn
level: medium
tags: [attack.T1059]
logsource:
  source: macos_unified_log
  event_type: process_spawn
detection:
  selection:
    process.image: /usr/bin/python3
  condition: selection
"""


class _StubChain:
    def __init__(self):
        self.appended = []
        self._seq = 1000

    def append(self, event_type, payload, agent_dna=None):
        self._seq += 1
        entry = type("Entry", (), {
            "seq": self._seq,
            "event_type": event_type,
            "event_data": payload,
            "agent_dna": agent_dna,
        })()
        self.appended.append(entry)
        return entry


# ---- Engine construction -------------------------------------------------


class TestEngineConstruction:
    def test_from_preloaded_rules(self):
        r1 = parse_rule(_RULE_SHELL)
        r2 = parse_rule(_RULE_PYTHON)
        e = DetectionEngine(rules=[r1, r2])
        assert e.ready()
        assert len(e.rules) == 2

    def test_from_dir(self, tmp_path):
        (tmp_path / "shell.yml").write_text(_RULE_SHELL, encoding="utf-8")
        (tmp_path / "python.yml").write_text(_RULE_PYTHON, encoding="utf-8")
        e = DetectionEngine(rules_dir=tmp_path)
        assert e.ready()
        assert len(e.rules) == 2

    def test_reload_refuses_swap_on_failure_keeps_prev(self, tmp_path):
        (tmp_path / "shell.yml").write_text(_RULE_SHELL, encoding="utf-8")
        e = DetectionEngine(rules_dir=tmp_path)
        assert e.ready() and len(e.rules) == 1

        # Add a broken rule + reload.
        (tmp_path / "bad.yml").write_text(
            "id: bad\ntitle: x\nlevel: high\ndetection: {s: {a: b}, condition: s}\n",
            encoding="utf-8",
        )  # missing tags
        e.reload_from_dir(tmp_path)
        assert not e.ready()
        assert e.load_errors, "load_errors should record the failure"
        # Previous rule set retained.
        assert len(e.rules) == 1

    def test_empty_dir_yields_empty_ready_engine(self, tmp_path):
        e = DetectionEngine(rules_dir=tmp_path)
        # No rules + no errors -> ready() still True (vacuous).
        assert e.ready()
        assert e.rules == ()


# ---- scan() -------------------------------------------------------------


class TestScan:
    def test_scan_with_no_rules_emits_nothing(self):
        e = DetectionEngine(rules=[])
        chain = _StubChain()
        ev = _make_event(payload={"process": {"image": "/bin/bash"}})
        result = e.scan("batch-1", [ev], audit_chain=chain)
        assert result.rules_evaluated == 0
        assert result.events_scanned == 1
        assert result.matches_by_rule == {}
        assert chain.appended == []

    def test_scan_with_matching_rule_emits_one_event(self):
        e = DetectionEngine(rules=[parse_rule(_RULE_SHELL)])
        chain = _StubChain()
        ev = _make_event(payload={"process": {"image": "/bin/bash"}})
        result = e.scan("batch-1", [ev], audit_chain=chain)
        assert len(chain.appended) == 1
        evt = chain.appended[0]
        assert evt.event_type == "detection_fired"
        assert evt.event_data["rule_id"] == "suspicious_shell_spawn"
        assert evt.event_data["batch_id"] == "batch-1"
        assert evt.event_data["technique"] == "attack.T1059.004"
        assert evt.event_data["severity"] == "high"
        assert evt.event_data["matched_event_ids"] == [ev.event_id]
        assert evt.event_data["match_count"] == 1
        assert result.matches_by_rule["suspicious_shell_spawn"] == [ev.event_id]
        assert result.audit_event_seqs == (evt.seq,)

    def test_scan_collapses_n_matches_into_one_chain_event(self):
        e = DetectionEngine(rules=[parse_rule(_RULE_SHELL)])
        chain = _StubChain()
        evs = [
            _make_event(payload={"process": {"image": "/bin/bash"}}, suffix="01"),
            _make_event(payload={"process": {"image": "/bin/bash"}}, suffix="02"),
            _make_event(payload={"process": {"image": "/bin/bash"}}, suffix="03"),
        ]
        result = e.scan("batch-2", evs, audit_chain=chain)
        assert len(chain.appended) == 1   # ONE event per (rule, batch)
        evt = chain.appended[0]
        assert evt.event_data["match_count"] == 3
        assert len(evt.event_data["matched_event_ids"]) == 3
        assert result.matches_by_rule["suspicious_shell_spawn"] == [
            e.event_id for e in evs
        ]

    def test_scan_emits_one_event_per_rule_per_batch(self):
        e = DetectionEngine(rules=[
            parse_rule(_RULE_SHELL),
            parse_rule(_RULE_PYTHON),
        ])
        chain = _StubChain()
        evs = [
            _make_event(payload={"process": {"image": "/bin/bash"}}, suffix="01"),
            _make_event(payload={"process": {"image": "/usr/bin/python3"}}, suffix="02"),
        ]
        result = e.scan("batch-3", evs, audit_chain=chain)
        assert len(chain.appended) == 2
        rule_ids_emitted = {e.event_data["rule_id"] for e in chain.appended}
        assert rule_ids_emitted == {"suspicious_shell_spawn", "python_spawn"}

    def test_scan_logsource_mismatch_skips_eval(self):
        e = DetectionEngine(rules=[parse_rule(_RULE_SHELL)])
        chain = _StubChain()
        ev = _make_event(
            source="other_source",
            payload={"process": {"image": "/bin/bash"}},
        )
        result = e.scan("batch-4", [ev], audit_chain=chain)
        assert result.matches_by_rule == {}
        assert chain.appended == []

    def test_scan_refuses_when_not_ready(self, tmp_path):
        """Engine with load errors should return empty result."""
        (tmp_path / "bad.yml").write_text("not valid yaml: : :", encoding="utf-8")
        e = DetectionEngine(rules_dir=tmp_path)
        assert not e.ready()
        chain = _StubChain()
        ev = _make_event(payload={"process": {"image": "/bin/bash"}})
        result = e.scan("batch-5", [ev], audit_chain=chain)
        assert result.rules_evaluated == 0
        assert chain.appended == []

    def test_scan_works_without_audit_chain(self):
        """Tests can call scan() without a chain — result still
        carries matches; just no chain side-effect."""
        e = DetectionEngine(rules=[parse_rule(_RULE_SHELL)])
        ev = _make_event(payload={"process": {"image": "/bin/bash"}})
        result = e.scan("batch-6", [ev])  # no audit_chain
        assert "suspicious_shell_spawn" in result.matches_by_rule
        assert result.audit_event_seqs == ()


# ---- AdapterIngestor integration ----------------------------------------


class _StubAdapter:
    SOURCE = "macos_unified_log"

    def command(self):
        return ["true"]

    def retention_override(self, e):
        return None

    def parse(self, line):
        # Each line is "<bash|python>:<suffix>" -> one event.
        kind, suffix = line.split(":")
        if kind == "bash":
            image = "/bin/bash"
        elif kind == "python":
            image = "/usr/bin/python3"
        else:
            return None
        ts = f"2026-05-18T10:00:{suffix}Z"
        payload = {"process": {"image": image}}
        ih = compute_integrity_hash(
            timestamp=ts, source=self.SOURCE,
            event_type="process_spawn", severity="info",
            payload=payload, correlation_id=None,
            retention_class="ephemeral",
        )
        return TelemetryEvent(
            event_id=ih[:16], timestamp=ts, source=self.SOURCE,
            event_type="process_spawn", severity="info",
            payload=payload, correlation_id=None,
            integrity_hash=ih, ingested_at="2026-05-18T10:00:00Z",
            retention_class="ephemeral",
        )


class TestIngestorIntegration:
    def test_flush_pending_calls_engine_after_chain_anchor(self, tmp_path):
        chain = _StubChain()
        store = SqliteTelemetryStore(tmp_path / "telemetry.sqlite")
        engine = DetectionEngine(rules=[parse_rule(_RULE_SHELL)])
        ing = AdapterIngestor(
            _StubAdapter(), store, batch_size=1000,
            audit_chain=chain, detection_engine=engine,
        )
        try:
            ing.inject_lines(["bash:01", "bash:02", "python:03"])
            batch_id = ing.flush_pending()
            assert batch_id is not None
        finally:
            store.close()

        # Chain has: 1 telemetry_batch_ingested + 1 detection_fired.
        types_in_order = [e.event_type for e in chain.appended]
        assert types_in_order == [
            "telemetry_batch_ingested",
            "detection_fired",
        ]
        df = chain.appended[1]
        assert df.event_data["rule_id"] == "suspicious_shell_spawn"
        # Only the two bash events matched.
        assert df.event_data["match_count"] == 2

    def test_no_engine_means_no_detection_events(self, tmp_path):
        chain = _StubChain()
        store = SqliteTelemetryStore(tmp_path / "telemetry.sqlite")
        ing = AdapterIngestor(
            _StubAdapter(), store, batch_size=1000,
            audit_chain=chain,
            # detection_engine omitted -> defaults to None
        )
        try:
            ing.inject_lines(["bash:01"])
            ing.flush_pending()
        finally:
            store.close()
        # Only the telemetry anchor; no detection_fired.
        assert [e.event_type for e in chain.appended] == [
            "telemetry_batch_ingested"
        ]

    def test_engine_failure_does_not_kill_ingest(self, tmp_path):
        """A bad engine.scan() impl shouldn't lose the chain anchor
        or the store data."""
        class _BadEngine:
            def scan(self, *a, **kw):
                raise RuntimeError("rule eval blew up")

        chain = _StubChain()
        store = SqliteTelemetryStore(tmp_path / "telemetry.sqlite")
        ing = AdapterIngestor(
            _StubAdapter(), store, batch_size=1000,
            audit_chain=chain, detection_engine=_BadEngine(),
        )
        try:
            ing.inject_lines(["bash:01"])
            batch_id = ing.flush_pending()
            assert batch_id is not None
            # Telemetry anchor still landed.
            assert [e.event_type for e in chain.appended] == [
                "telemetry_batch_ingested"
            ]
            # Stats reflects the engine failure.
            assert "detection_engine.scan failed" in (ing.stats.last_error or "")
        finally:
            store.close()
