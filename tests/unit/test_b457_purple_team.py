"""B457 (ADR-0066 T4) — purple_team scenario DSL + ScenarioRunner.

Covers:
  - the scenario parser accepts a well-formed scenario and rejects
    every malformed shape with a clear error
  - the runner materialises synthetic events with provenance,
    replays them through a production DetectionEngine in simulation
    mode, and reports coverage (detected / coverage_gap)
  - the only chain event the runner emits is
    `purple_team_run_completed` — synthetic detections never reach
    a chain
"""
from __future__ import annotations

import textwrap
from typing import Any

import pytest

from forest_soul_forge.security.detection.events import DetectionRule
from forest_soul_forge.security.detection.engine import DetectionEngine
from forest_soul_forge.security.playbook import PlaybookEngine, parse_playbook
from forest_soul_forge.security.purple_team import (
    ScenarioError,
    ScenarioRunner,
    parse_scenario,
    parse_scenarios_from_dir,
)
from forest_soul_forge.security.telemetry.store import SqliteTelemetryStore


# A scenario emulating osascript shell spawn (ATT&CK T1059.002).
SCENARIO = textwrap.dedent("""
    scenario_id: shell-spawn-osascript
    version: '1'
    description: Emulates an osascript interpreter spawn.
    technique: attack.T1059.002
    events:
      - source: macos_unified_log
        event_type: process_spawn
        severity: warn
        payload:
          process:
            image: /usr/bin/osascript
    expect:
      detection_rule_id: osascript_spawn
""").strip()


def _detection_rule(rule_id: str = "osascript_spawn") -> DetectionRule:
    return DetectionRule(
        rule_id=rule_id,
        title="osascript spawn",
        description="d",
        rule_version="a" * 64,
        level="high",
        tags=("attack.T1059.002",),
        logsource_source="macos_unified_log",
        logsource_event_type="process_spawn",
        selections={"sel": {"process.image": "/usr/bin/osascript"}},
        condition="sel",
    )


class _StubChain:
    def __init__(self) -> None:
        self.appended: list[Any] = []
        self._seq = 9000

    def append(self, event_type, event_data, agent_dna=None):
        self._seq += 1
        entry = type("Entry", (), {
            "seq": self._seq, "event_type": event_type,
            "event_data": event_data, "agent_dna": agent_dna,
        })()
        self.appended.append(entry)
        return entry

    def tail(self, n: int) -> list[Any]:
        return list(reversed(self.appended[-n:])) if n > 0 else []


# ---- parser --------------------------------------------------------------


def test_scenario_parses():
    sc = parse_scenario(SCENARIO)
    assert sc.scenario_id == "shell-spawn-osascript"
    assert sc.technique == "attack.T1059.002"
    assert len(sc.events) == 1
    assert sc.events[0].event_type == "process_spawn"
    assert sc.expected_detection_rule_id == "osascript_spawn"
    assert len(sc.scenario_version) == 64


def test_missing_scenario_id_rejected():
    with pytest.raises(ScenarioError, match="scenario_id"):
        parse_scenario("version: '1'\ntechnique: x\nevents: []")


def test_missing_technique_rejected():
    body = SCENARIO.replace("technique: attack.T1059.002\n", "")
    with pytest.raises(ScenarioError, match="technique"):
        parse_scenario(body)


def test_empty_events_rejected():
    body = textwrap.dedent("""
        scenario_id: x
        version: '1'
        technique: attack.T1059
        events: []
    """).strip()
    with pytest.raises(ScenarioError, match="events"):
        parse_scenario(body)


def test_bad_event_type_rejected():
    body = SCENARIO.replace("event_type: process_spawn", "event_type: telepathy")
    with pytest.raises(ScenarioError, match="EVENT_TYPES"):
        parse_scenario(body)


def test_bad_severity_rejected():
    body = SCENARIO.replace("severity: warn", "severity: catastrophic")
    with pytest.raises(ScenarioError, match="SEVERITIES"):
        parse_scenario(body)


def test_duplicate_scenario_id_in_dir(tmp_path):
    (tmp_path / "a.yml").write_text(SCENARIO, encoding="utf-8")
    (tmp_path / "b.yml").write_text(SCENARIO, encoding="utf-8")
    parsed, failed = parse_scenarios_from_dir(tmp_path)
    assert any("duplicate scenario_id" in str(e) for _, e in failed)


# ---- runner: coverage measurement ---------------------------------------


def test_runner_detects_expected_technique():
    engine = DetectionEngine(rules=[_detection_rule("osascript_spawn")])
    runner = ScenarioRunner(scenarios=[parse_scenario(SCENARIO)])
    result = runner.run_scenario(runner.scenarios[0], engine)
    assert result.detected is True
    assert "osascript_spawn" in result.detected_rule_ids
    assert result.coverage_gap is False
    assert result.events_emitted == 1


def test_runner_reports_coverage_gap():
    """The SOC has NO rule for this technique → coverage gap."""
    engine = DetectionEngine(rules=[])   # empty rule set
    runner = ScenarioRunner(scenarios=[parse_scenario(SCENARIO)])
    result = runner.run_scenario(runner.scenarios[0], engine)
    assert result.detected is False
    assert result.coverage_gap is True   # expected osascript_spawn, none fired


def test_runner_emits_purple_team_run_completed_only():
    """The runner must emit purple_team_run_completed and NOTHING
    else — synthetic detections never reach the chain."""
    engine = DetectionEngine(rules=[_detection_rule()])
    runner = ScenarioRunner(scenarios=[parse_scenario(SCENARIO)])
    chain = _StubChain()
    runner.run_scenario(runner.scenarios[0], engine, audit_chain=chain)
    event_types = [e.event_type for e in chain.appended]
    assert event_types == ["purple_team_run_completed"]
    ed = chain.appended[0].event_data
    assert ed["simulation"] is True
    assert ed["technique"] == "attack.T1059.002"
    assert ed["detected"] is True
    assert "coverage_note" in ed


def test_runner_writes_synthetic_events_to_sim_store(tmp_path):
    engine = DetectionEngine(rules=[_detection_rule()])
    runner = ScenarioRunner(scenarios=[parse_scenario(SCENARIO)])
    sim_store = SqliteTelemetryStore(tmp_path / "telemetry_simulation.sqlite")
    result = runner.run_scenario(runner.scenarios[0], engine, sim_store=sim_store)
    # The synthetic event landed in the sim store, provenance-stamped.
    stored = sim_store.query(event_type="process_spawn", limit=10)
    assert len(stored) == 1
    assert stored[0].payload["simulation"] is True
    assert stored[0].payload["purple_team_run_id"] == result.run_id


def test_runner_measures_response_with_playbook_engine():
    engine = DetectionEngine(rules=[_detection_rule("osascript_spawn")])
    playbook = parse_playbook(textwrap.dedent("""
        playbook_id: respond-osascript
        version: '1'
        trigger:
          detection_rule_ids: [osascript_spawn]
          min_severity: high
          cooldown_seconds: 0
        approval:
          default: required_human
          steps_auto_approved: [notify]
        steps:
          - id: notify
            action: delegate
            args: {message: "osascript spawn"}
    """).strip())
    pb_engine = PlaybookEngine(playbooks=[playbook])
    runner = ScenarioRunner(scenarios=[parse_scenario(SCENARIO)])
    result = runner.run_scenario(
        runner.scenarios[0], engine, playbook_engine=pb_engine,
    )
    assert result.responded is True


def test_run_all_blocked_when_not_ready(tmp_path):
    (tmp_path / "bad.yml").write_text("scenario_id: x\n", encoding="utf-8")
    runner = ScenarioRunner(scenarios_dir=tmp_path)
    assert runner.ready() is False
    engine = DetectionEngine(rules=[])
    assert runner.run_all(engine) == []


def test_run_all_runs_every_scenario(tmp_path):
    (tmp_path / "a.yml").write_text(SCENARIO, encoding="utf-8")
    (tmp_path / "b.yml").write_text(
        SCENARIO.replace("shell-spawn-osascript", "shell-spawn-two"),
        encoding="utf-8",
    )
    runner = ScenarioRunner(scenarios_dir=tmp_path)
    assert runner.ready() is True
    engine = DetectionEngine(rules=[_detection_rule()])
    results = runner.run_all(engine)
    assert len(results) == 2
    assert all(r.detected for r in results)
