"""ScenarioDef + ScenarioEvent + PurpleTeamRunResult — the dataclass
surface for purple_pete's adversary-simulation substrate.

ADR-0066 T4 (B457). The parser (parser.py) produces ScenarioDef
instances; the runner (runner.py) materialises each scenario's
synthetic events, replays them through the production DetectionEngine
in simulation mode, measures coverage, and emits one
`purple_team_run_completed` audit chain entry per run.

A scenario is a curated, operator-authored sequence of synthetic
telemetry events that emulates one ATT&CK technique, plus the
detection rule the SOC *should* catch it with. purple_pete runs
these against the SOC's real rule set to answer the question "did
our detection coverage actually catch technique X?" — turning a
manual exercise into a recurring measurement.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


class ScenarioError(ValueError):
    """Raised by the parser when a scenario violates the ADR-0066
    scenario DSL contract. Distinct from ValueError so callers can
    catch this class specifically."""


def scenario_version_hash(scenario_body: str) -> str:
    """sha256 of the scenario's canonicalised YAML body (hex).

    Recorded as `scenario_version` on every `purple_team_run_completed`
    audit event so a coverage measurement pins the exact scenario
    that produced it. Mirrors detection's rule_version_hash and
    playbook's playbook_version_hash."""
    return hashlib.sha256(scenario_body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScenarioEvent:
    """One synthetic telemetry event in a scenario.

    Carries only the source-authored fields. The runner derives the
    event_id, timestamps, and integrity_hash at materialisation time,
    and injects the purple-team provenance marker into the payload
    (ADR-0066 `require_scenario_provenance`) — the operator never
    hand-writes those.
    """

    source: str
    event_type: str            # MUST be a telemetry EVENT_TYPES value
    severity: str              # MUST be a telemetry SEVERITIES value
    payload: dict[str, Any]


@dataclass(frozen=True)
class ScenarioDef:
    """A parsed purple-team scenario.

    Immutable; the parser builds and the runner reads. Subclassing
    is forbidden — same posture as DetectionRule / PlaybookDef.
    """

    scenario_id: str
    version: str                       # operator-authored version string
    scenario_version: str              # sha256 of canonical body
    description: str
    technique: str                     # ATT&CK technique this emulates
    events: tuple[ScenarioEvent, ...]
    # The detection rule the SOC should catch this scenario with.
    # None means the scenario is a pure coverage probe with no
    # specific expectation — the run records whatever fired.
    expected_detection_rule_id: str | None

    def __post_init__(self) -> None:
        if not self.events:
            raise ScenarioError(
                f"scenario {self.scenario_id!r}: at least one event is required"
            )
        if not self.technique:
            raise ScenarioError(
                f"scenario {self.scenario_id!r}: 'technique' is required "
                f"(the ATT&CK technique this scenario emulates)"
            )


@dataclass(frozen=True)
class PurpleTeamRunResult:
    """The outcome of running one scenario against the SOC.

    `coverage_gap` is the headline signal: True when the scenario
    declared an `expected_detection_rule_id` that did NOT fire — the
    SOC missed a technique it was supposed to catch.
    """

    scenario_id: str
    scenario_version: str
    technique: str
    run_id: str
    events_emitted: int
    expected_detection_rule_id: str | None
    detected: bool                     # did ANY detection rule fire?
    detected_rule_ids: tuple[str, ...]
    coverage_gap: bool                 # expected a rule that did not fire
    time_to_detect_ms: int | None      # synthetic emit → first match
    responded: bool                    # did a playbook resolve a response?
    time_to_respond_ms: int | None     # first match → playbook resolution
    audit_event_seq: int | None
