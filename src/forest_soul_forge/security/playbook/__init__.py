"""SOAR playbook substrate (ADR-0066).

The package that implements operator-authored response playbooks —
the codified response surface that consumes `detection_fired` events
(ADR-0065) and dispatches actions under explicit approval
governance.

T1 (B454) ships:
  - PlaybookDef + PlaybookStep + PlaybookTrigger dataclasses (events.py)
  - the playbook YAML parser (parser.py)

T2 (B455) lands the PlaybookEngine — trigger resolution, per-target
cooldown, approval gating, `playbook_executed` audit emission.
T3 adds playbook_pilot, T4 adds purple_pete, T5 ships the runbook +
starter libraries, T6 closes the arc.

The DSL shipped here is intentionally narrow (ADR-0066 D1): every
step is one catalog-defined tool or skill invocation. No conditional
branches, no loops — composition over nesting. Each playbook
declares a trigger (which detections fire it), an approval block
(default-deny; explicit allowlist), and an ordered step list.
"""
from forest_soul_forge.security.playbook.engine import (
    PlaybookEngine,
    PlaybookProcessResult,
    PlaybookRunResult,
    PlaybookStepOutcome,
)
from forest_soul_forge.security.playbook.events import (
    PlaybookDef,
    PlaybookError,
    PlaybookStep,
    PlaybookTrigger,
    playbook_version_hash,
    severity_rank,
)
from forest_soul_forge.security.playbook.parser import (
    parse_playbook,
    parse_playbooks_from_dir,
)

__all__ = [
    "PlaybookDef",
    "PlaybookStep",
    "PlaybookTrigger",
    "PlaybookError",
    "playbook_version_hash",
    "severity_rank",
    "parse_playbook",
    "parse_playbooks_from_dir",
    "PlaybookEngine",
    "PlaybookProcessResult",
    "PlaybookRunResult",
    "PlaybookStepOutcome",
]
