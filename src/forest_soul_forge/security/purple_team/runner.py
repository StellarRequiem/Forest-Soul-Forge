"""ScenarioRunner — purple_pete's adversary-simulation runner.

ADR-0066 T4 (B457). The runner owns the parsed scenario set and,
for each scenario, materialises its synthetic events, replays them
through the production DetectionEngine in simulation mode, measures
coverage, and emits one `purple_team_run_completed` audit chain
event per run.

Simulation isolation — how this satisfies ADR-0066 Decision 3
without modifying the DetectionEngine or coupling to ADR-0073
sub-chain segmentation:

  - Synthetic events are written ONLY to the operator-supplied
    simulation TelemetryStore (`data/telemetry_simulation.sqlite`),
    never to the production store. The constitution policy
    `forbid_production_telemetry_emit` enforces the same at the
    governance layer; the runner simply has no production-store
    handle to misuse.
  - The DetectionEngine is invoked with `audit_chain=None` — its
    existing no-emit path. Synthetic detections therefore never
    reach ANY audit chain; there is nothing to filter out and no
    sub-chain to segment.
  - The ONLY thing recorded on the real chain is the
    `purple_team_run_completed` summary — its own event type,
    carrying `simulation: true` provenance, so production reviewers
    distinguish it trivially.

Every synthetic event carries `purple_team_run_id` + `simulation`
in its payload (ADR-0066 `require_scenario_provenance`); the
runner injects them, the operator never hand-writes them.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from forest_soul_forge.security.purple_team.events import (
    PurpleTeamRunResult,
    ScenarioDef,
    ScenarioError,
)
from forest_soul_forge.security.purple_team.parser import parse_scenarios_from_dir
from forest_soul_forge.security.telemetry.events import (
    TelemetryEvent,
    compute_integrity_hash,
)

if TYPE_CHECKING:
    from forest_soul_forge.core.audit_chain import AuditChain
    from forest_soul_forge.security.detection.engine import DetectionEngine
    from forest_soul_forge.security.playbook.engine import PlaybookEngine
    from forest_soul_forge.security.telemetry.store import TelemetryStore


log = logging.getLogger(__name__)


class ScenarioRunner:
    """Holds the scenario set + runs scenarios against the SOC.

    Hot-reload is via `reload_from_dir()`. Construction never raises
    on a single bad scenario — `load_errors` carries the punch list,
    `ready()` is the gate. Mirrors DetectionEngine / PlaybookEngine.
    """

    def __init__(
        self,
        scenarios_dir: Path | None = None,
        *,
        scenarios: Iterable[ScenarioDef] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._scenarios: tuple[ScenarioDef, ...] = ()
        self.load_errors: list[tuple[Path, ScenarioError]] = []
        self.scenarios_dir = scenarios_dir
        if scenarios is not None:
            with self._lock:
                self._scenarios = tuple(scenarios)
        elif scenarios_dir is not None:
            self.reload_from_dir(scenarios_dir)

    @property
    def scenarios(self) -> tuple[ScenarioDef, ...]:
        with self._lock:
            return self._scenarios

    def ready(self) -> bool:
        """True iff zero load errors."""
        return not self.load_errors

    def reload_from_dir(self, directory: Path) -> None:
        """Re-parse + atomically swap the scenario set. If ANY
        scenario fails to parse the previous set is retained and the
        failures recorded in load_errors (mirrors the detection /
        playbook engines)."""
        parsed, failed = parse_scenarios_from_dir(directory)
        with self._lock:
            if failed:
                self.load_errors = list(failed)
                log.warning(
                    "ScenarioRunner: reload refused — %d failure(s) "
                    "(previous set retained)", len(failed),
                )
                return
            self.load_errors = []
            self._scenarios = tuple(parsed)
            log.info(
                "ScenarioRunner: loaded %d scenario(s) from %s",
                len(self._scenarios), directory,
            )

    # ----- running --------------------------------------------------------

    def run_scenario(
        self,
        scenario: ScenarioDef,
        detection_engine: "DetectionEngine",
        *,
        sim_store: "TelemetryStore | None" = None,
        audit_chain: "AuditChain | None" = None,
        playbook_engine: "PlaybookEngine | None" = None,
        agent_dna: str | None = None,
        now: float | None = None,
    ) -> PurpleTeamRunResult:
        """Run one scenario against the SOC's detection coverage.

        `detection_engine` is the PRODUCTION engine — the whole point
        is to test the real rule set. `sim_store`, `audit_chain` and
        `playbook_engine` are all OPTIONAL: when None the runner
        still materialises events + measures coverage and returns
        the result, so tests can inspect without standing up the
        full substrate (the same posture as DetectionEngine.scan).
        """
        wall = time.time() if now is None else now
        run_id = uuid.uuid4().hex

        sim_events = self._materialise_events(scenario, run_id, wall)

        # Synthetic events go ONLY to the simulation store. The
        # runner never receives a production-store handle.
        batch_id = f"purpleteam-{run_id[:12]}"
        if sim_store is not None:
            try:
                batch_id = sim_store.ingest_batch(sim_events)
            except Exception as e:
                log.warning(
                    "ScenarioRunner: sim_store.ingest_batch failed for "
                    "scenario %r: %r", scenario.scenario_id, e,
                )

        # Replay through the production DetectionEngine with
        # audit_chain=None — the no-emit path. Synthetic detections
        # never reach a chain.
        t0 = time.perf_counter()
        scan = detection_engine.scan(batch_id, sim_events, audit_chain=None)
        t1 = time.perf_counter()

        detected_rule_ids = tuple(sorted(scan.matches_by_rule.keys()))
        detected = bool(detected_rule_ids)
        ttd_ms = int((t1 - t0) * 1000) if detected else None
        coverage_gap = (
            scenario.expected_detection_rule_id is not None
            and scenario.expected_detection_rule_id not in detected_rule_ids
        )

        responded, ttr_ms = self._measure_response(
            detection_engine, playbook_engine, detected_rule_ids,
            scan, batch_id,
        )

        result = PurpleTeamRunResult(
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.scenario_version,
            technique=scenario.technique,
            run_id=run_id,
            events_emitted=len(sim_events),
            expected_detection_rule_id=scenario.expected_detection_rule_id,
            detected=detected,
            detected_rule_ids=detected_rule_ids,
            coverage_gap=coverage_gap,
            time_to_detect_ms=ttd_ms,
            responded=responded,
            time_to_respond_ms=ttr_ms,
            audit_event_seq=None,
        )

        audit_seq = self._emit_run_completed(result, audit_chain, agent_dna)
        if audit_seq is not None:
            # PurpleTeamRunResult is frozen — rebuild with the seq.
            result = PurpleTeamRunResult(
                **{**result.__dict__, "audit_event_seq": audit_seq}
            )
        return result

    def run_all(
        self,
        detection_engine: "DetectionEngine",
        *,
        sim_store: "TelemetryStore | None" = None,
        audit_chain: "AuditChain | None" = None,
        playbook_engine: "PlaybookEngine | None" = None,
        agent_dna: str | None = None,
    ) -> list[PurpleTeamRunResult]:
        """Run every loaded scenario. Returns one result per
        scenario. Refuses (returns []) when the runner is not
        ready — a single bad scenario blocks the set, mirroring the
        detection / playbook engines."""
        if not self.ready():
            return []
        return [
            self.run_scenario(
                sc, detection_engine, sim_store=sim_store,
                audit_chain=audit_chain, playbook_engine=playbook_engine,
                agent_dna=agent_dna,
            )
            for sc in self.scenarios
        ]

    # ----- internals ------------------------------------------------------

    def _materialise_events(
        self, scenario: ScenarioDef, run_id: str, wall: float,
    ) -> list[TelemetryEvent]:
        """Turn a scenario's ScenarioEvents into real TelemetryEvents.

        Each synthetic event's payload is stamped with the
        purple-team provenance marker (ADR-0066
        `require_scenario_provenance`) BEFORE the integrity hash is
        computed, so the marker is covered by the hash. retention
        is `ephemeral` — synthetic events are short-lived by design.
        correlation_id is the run_id, so a whole scenario run is one
        queryable correlation group in the simulation store.
        """
        iso = datetime.fromtimestamp(wall, tz=timezone.utc).isoformat()
        out: list[TelemetryEvent] = []
        for idx, ev in enumerate(scenario.events):
            payload = {
                **ev.payload,
                "simulation": True,
                "purple_team_run_id": run_id,
                "scenario_id": scenario.scenario_id,
            }
            ih = compute_integrity_hash(
                timestamp=iso,
                source=ev.source,
                event_type=ev.event_type,
                severity=ev.severity,
                payload=payload,
                correlation_id=run_id,
                retention_class="ephemeral",
            )
            out.append(TelemetryEvent(
                event_id=f"sim-{run_id[:12]}-{idx:03d}",
                timestamp=iso,
                source=ev.source,
                event_type=ev.event_type,
                severity=ev.severity,
                payload=payload,
                correlation_id=run_id,
                integrity_hash=ih,
                ingested_at=iso,
                retention_class="ephemeral",
            ))
        return out

    def _measure_response(
        self,
        detection_engine: "DetectionEngine",
        playbook_engine: "PlaybookEngine | None",
        detected_rule_ids: tuple[str, ...],
        scan: Any,
        batch_id: str,
    ) -> tuple[bool, int | None]:
        """If a detection fired and a PlaybookEngine is supplied,
        measure whether a playbook would respond. Like detection,
        the playbook engine runs with audit_chain=None — the
        response is simulated, never recorded as a real
        playbook_executed event."""
        if playbook_engine is None or not detected_rule_ids:
            return (False, None)

        rules_by_id = {r.rule_id: r for r in detection_engine.rules}
        responded = False
        t0 = time.perf_counter()
        for rule_id in detected_rule_ids:
            rule = rules_by_id.get(rule_id)
            detection = {
                "rule_id": rule_id,
                "severity": rule.level if rule else "medium",
                "technique": rule.tags[0] if rule and rule.tags else "attack.unknown",
                "matched_event_ids": scan.matches_by_rule.get(rule_id, []),
                "batch_id": batch_id,
            }
            proc = playbook_engine.process_detection(detection, audit_chain=None)
            if proc.runs:
                responded = True
        t1 = time.perf_counter()
        return (responded, int((t1 - t0) * 1000) if responded else None)

    def _emit_run_completed(
        self,
        result: PurpleTeamRunResult,
        audit_chain: "AuditChain | None",
        agent_dna: str | None,
    ) -> int | None:
        """Emit the ADR-0066 §3 `purple_team_run_completed` event.

        This is the ONLY thing the runner writes to the real audit
        chain — the coverage measurement. It carries `simulation:
        true` provenance so production reviewers filter it from real
        SOC activity."""
        if audit_chain is None:
            return None
        try:
            entry = audit_chain.append(
                "purple_team_run_completed",
                {
                    "scenario_id":      result.scenario_id,
                    "scenario_version": result.scenario_version,
                    "technique":        result.technique,
                    "run_id":           result.run_id,
                    "simulation":       True,
                    "events_emitted":   result.events_emitted,
                    "expected_detection_rule_id":
                        result.expected_detection_rule_id,
                    "detected":         result.detected,
                    "detected_rule_ids": list(result.detected_rule_ids),
                    "coverage_gap":     result.coverage_gap,
                    "time_to_detect_ms": result.time_to_detect_ms,
                    "responded":        result.responded,
                    "time_to_respond_ms": result.time_to_respond_ms,
                    "coverage_note":    _coverage_note(result),
                    "completed_at":     time.time(),
                },
                agent_dna=agent_dna,
            )
            seq = getattr(entry, "seq", None)
            return seq if isinstance(seq, int) else None
        except Exception as e:
            log.warning(
                "ScenarioRunner: chain append failed for scenario "
                "%r: %r", result.scenario_id, e,
            )
            return None


def _coverage_note(result: PurpleTeamRunResult) -> str:
    """Human-readable one-liner for the run — the ADR-0066 §3
    example shape ("technique T1059.004 detected in 1.4s; technique
    T1003 NOT detected")."""
    if result.coverage_gap:
        return (
            f"technique {result.technique} NOT detected — coverage gap "
            f"(expected rule {result.expected_detection_rule_id!r})"
        )
    if result.detected:
        ttd = result.time_to_detect_ms
        by = ", ".join(result.detected_rule_ids)
        return (
            f"technique {result.technique} detected by [{by}] "
            f"in {ttd}ms"
        )
    return (
        f"technique {result.technique} produced no detection "
        f"(no expectation was declared)"
    )
