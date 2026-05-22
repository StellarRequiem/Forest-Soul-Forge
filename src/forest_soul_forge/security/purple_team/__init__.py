"""Purple-team adversary-simulation substrate (ADR-0066).

The package that implements purple_pete's continuous coverage
measurement — synthetic ATT&CK-technique scenarios replayed against
the SOC's real detection rule set to answer "did our SOC actually
catch technique X?".

T4 (B457) ships:
  - ScenarioDef + ScenarioEvent + PurpleTeamRunResult dataclasses
    (events.py)
  - the scenario YAML parser (parser.py)
  - the ScenarioRunner (runner.py) — materialises synthetic events,
    replays them through the production DetectionEngine in
    simulation mode, measures coverage, emits
    `purple_team_run_completed` audit events

Simulation isolation: synthetic events are written ONLY to the
operator-supplied simulation telemetry store; detection replay runs
with `audit_chain=None` so synthetic detections never reach any
chain; the only thing recorded on the real chain is the
`purple_team_run_completed` summary, on its own event type with
`simulation: true` provenance. See runner.py for the full rationale
vs. ADR-0066 Decision 3.
"""
from forest_soul_forge.security.purple_team.events import (
    PurpleTeamRunResult,
    ScenarioDef,
    ScenarioError,
    ScenarioEvent,
    scenario_version_hash,
)
from forest_soul_forge.security.purple_team.parser import (
    parse_scenario,
    parse_scenarios_from_dir,
)
from forest_soul_forge.security.purple_team.runner import ScenarioRunner

__all__ = [
    "ScenarioDef",
    "ScenarioEvent",
    "ScenarioError",
    "PurpleTeamRunResult",
    "scenario_version_hash",
    "parse_scenario",
    "parse_scenarios_from_dir",
    "ScenarioRunner",
]
