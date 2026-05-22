"""Purple-team scenario YAML parser.

ADR-0066 T4 (B457). Takes a YAML body (or a dict) and produces a
ScenarioDef. Rejects any feature outside the scenario DSL with a
clear error so the operator knows exactly what needs to change.

parse_scenarios_from_dir returns (parsed, failed) rather than
raising on the first bad file — the runner's loader and section-01
of the diagnostic harness both call it. Mirrors detection/parser.py
and playbook/parser.py exactly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from forest_soul_forge.security.purple_team.events import (
    ScenarioDef,
    ScenarioError,
    ScenarioEvent,
    scenario_version_hash,
)
from forest_soul_forge.security.telemetry.events import EVENT_TYPES, SEVERITIES


def parse_scenario(
    scenario_body: str, *, source_path: str | None = None
) -> ScenarioDef:
    """Parse a YAML scenario body into a ScenarioDef.

    source_path is included in error messages for operator
    diagnostics — purely informational; the parser doesn't read
    from disk on its own.
    """
    if not isinstance(scenario_body, str) or not scenario_body.strip():
        raise ScenarioError(
            f"scenario body is empty"
            f"{' at ' + source_path if source_path else ''}"
        )

    try:
        doc = yaml.safe_load(scenario_body)
    except yaml.YAMLError as e:
        raise ScenarioError(
            f"YAML parse error"
            f"{' at ' + source_path if source_path else ''}: {e}"
        ) from e

    if not isinstance(doc, dict):
        raise ScenarioError(
            f"scenario body must parse to a mapping; got "
            f"{type(doc).__name__}"
            f"{' at ' + source_path if source_path else ''}"
        )

    return _parse_scenario_dict(doc, source_path)


def _parse_scenario_dict(
    doc: dict[str, Any], source_path: str | None
) -> ScenarioDef:
    where = f" ({source_path})" if source_path else ""

    scenario_id = doc.get("scenario_id") or doc.get("id")
    if not scenario_id or not isinstance(scenario_id, str):
        raise ScenarioError(f"scenario missing 'scenario_id'{where}")

    version = doc.get("version")
    if isinstance(version, (int, float)):
        version = str(version)
    if not version or not isinstance(version, str):
        raise ScenarioError(
            f"{scenario_id!r}: 'version' is required (string){where}"
        )

    description = doc.get("description") or ""
    if not isinstance(description, str):
        raise ScenarioError(
            f"{scenario_id!r}: description must be a string{where}"
        )

    technique = doc.get("technique")
    if not technique or not isinstance(technique, str):
        raise ScenarioError(
            f"{scenario_id!r}: 'technique' is required — the ATT&CK "
            f"technique this scenario emulates (e.g. attack.T1059.002)"
            f"{where}"
        )

    events = _parse_events(scenario_id, doc.get("events"), where)

    # `expect` is optional. The only key v1 reads is
    # detection_rule_id — the rule the SOC should catch this with.
    expect = doc.get("expect") or {}
    if expect and not isinstance(expect, dict):
        raise ScenarioError(
            f"{scenario_id!r}: 'expect' must be a mapping{where}"
        )
    expected_rule = expect.get("detection_rule_id")
    if expected_rule is not None and not isinstance(expected_rule, str):
        raise ScenarioError(
            f"{scenario_id!r}: expect.detection_rule_id must be a "
            f"string when set{where}"
        )

    canonical = yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)

    return ScenarioDef(
        scenario_id=scenario_id,
        version=version,
        scenario_version=scenario_version_hash(canonical),
        description=description,
        technique=technique,
        events=events,
        expected_detection_rule_id=expected_rule,
    )


def _parse_events(
    scenario_id: str, raw: Any, where: str
) -> tuple[ScenarioEvent, ...]:
    if not isinstance(raw, list) or not raw:
        raise ScenarioError(
            f"{scenario_id!r}: 'events' must be a non-empty list{where}"
        )

    events: list[ScenarioEvent] = []
    for idx, raw_ev in enumerate(raw):
        if not isinstance(raw_ev, dict):
            raise ScenarioError(
                f"{scenario_id!r}: event #{idx} must be a mapping; got "
                f"{type(raw_ev).__name__}{where}"
            )

        source = raw_ev.get("source")
        if not source or not isinstance(source, str):
            raise ScenarioError(
                f"{scenario_id!r}: event #{idx} missing 'source'{where}"
            )

        event_type = raw_ev.get("event_type")
        # The runner materialises ScenarioEvents into real
        # TelemetryEvents, which enforce the closed EVENT_TYPES enum
        # — validate here too so the operator sees the gap at parse
        # time, not at run time.
        if event_type not in EVENT_TYPES:
            raise ScenarioError(
                f"{scenario_id!r}: event #{idx} event_type "
                f"{event_type!r} not in telemetry EVENT_TYPES "
                f"{sorted(EVENT_TYPES)}{where}"
            )

        severity = raw_ev.get("severity")
        if severity not in SEVERITIES:
            raise ScenarioError(
                f"{scenario_id!r}: event #{idx} severity {severity!r} "
                f"not in telemetry SEVERITIES {sorted(SEVERITIES)}{where}"
            )

        payload = raw_ev.get("payload") or {}
        if not isinstance(payload, dict):
            raise ScenarioError(
                f"{scenario_id!r}: event #{idx} payload must be a "
                f"mapping; got {type(payload).__name__}{where}"
            )

        events.append(ScenarioEvent(
            source=source,
            event_type=event_type,
            severity=severity,
            payload=dict(payload),
        ))

    return tuple(events)


def parse_scenarios_from_dir(
    directory: Path,
) -> tuple[list[ScenarioDef], list[tuple[Path, ScenarioError]]]:
    """Walk a directory of *.yml files and parse each. Returns
    (parsed, failed) — failed entries carry the path + error so the
    caller can surface them as a punch list. Mirrors
    detection/playbook parse_*_from_dir."""
    parsed: list[ScenarioDef] = []
    failed: list[tuple[Path, ScenarioError]] = []

    if not directory.exists():
        return (parsed, failed)

    for path in sorted(directory.glob("*.yml")):
        body = path.read_text(encoding="utf-8")
        try:
            parsed.append(parse_scenario(body, source_path=str(path)))
        except ScenarioError as e:
            failed.append((path, e))

    # Duplicate scenario_id detection.
    seen: set[str] = set()
    for sc in parsed:
        if sc.scenario_id in seen:
            failed.append((
                Path(""),
                ScenarioError(
                    f"duplicate scenario_id {sc.scenario_id!r}: appears "
                    f"in multiple files"
                ),
            ))
        seen.add(sc.scenario_id)
    return (parsed, failed)
