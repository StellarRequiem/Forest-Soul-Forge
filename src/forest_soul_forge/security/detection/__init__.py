"""Detection-as-code substrate (ADR-0065).

The package that implements continuous detection over the
telemetry stream. T1 ships:
  - DetectionRule + DetectionMatch dataclasses (events.py)
  - Sigma-subset YAML parser (parser.py)

T2 lands the engine + scan integration. T3 adds the
detection_engineer role. T4 wires the harness. T5 ships the
runbook + starter rules. T6 closes the arc.

The Sigma subset shipped here is intentionally narrow:
  - logsource (mapped to TelemetryEvent.source + event_type)
  - detection.<selection_name>: field/value match dicts
  - detection.condition: boolean expression over selection names
  - level: severity passthrough
  - tags: MITRE ATT&CK technique IDs (mandatory per ADR-0065 D3)
"""
from forest_soul_forge.security.detection.events import (
    DetectionMatch,
    DetectionRule,
    DetectionRuleError,
)
from forest_soul_forge.security.detection.parser import (
    parse_rule,
    parse_rules_from_dir,
)

__all__ = [
    "DetectionRule",
    "DetectionMatch",
    "DetectionRuleError",
    "parse_rule",
    "parse_rules_from_dir",
]
