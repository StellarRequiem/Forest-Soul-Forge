"""DetectionEngine — per-batch rule evaluator.

ADR-0065 T2 (B390). The engine owns the parsed rule set and runs
it against every telemetry batch as it's anchored in the audit
chain. Matches collapse into one `detection_fired` audit chain
event per (rule, batch) pair with the full matched_event_ids
list.

Per ADR-0065 D7, the engine refuses to come up if any rule fails
to parse — the operator must repair the rule set (or remove the
bad rule) before the engine resumes. The lifespan caller checks
`load_errors` before wiring the engine into the ingestor.

The engine is synchronous (matches finish before flush_pending
returns). The rule set is small today (tens of rules) so per-batch
overhead stays in the millisecond range. T-future adds an async
queue when rule-set growth or batch size pushes past that budget.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from forest_soul_forge.security.detection.events import (
    DetectionMatch,
    DetectionRule,
    DetectionRuleError,
)
from forest_soul_forge.security.detection.parser import parse_rules_from_dir

if TYPE_CHECKING:
    from forest_soul_forge.core.audit_chain import AuditChain
    from forest_soul_forge.security.telemetry.events import TelemetryEvent


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectionScanResult:
    """Per-batch scan summary — useful for tests + the harness.

    Engines emit chain events as a side effect, but tests need a
    cheap inspection surface that doesn't require walking the
    chain. The result also tells the operator how many events
    were scanned + how many rules fired per batch.
    """
    batch_id: str
    rules_evaluated: int
    events_scanned: int
    matches_by_rule: dict[str, list[str]]   # rule_id -> matched event_ids
    audit_event_seqs: tuple[int, ...]       # seqs of emitted detection_fired
    scan_ms: int


class DetectionEngine:
    """Holds the rule set + scans batches.

    Hot-reload is via `reload_from_dir()` — the lifespan caller
    POSTs /detections/reload (T-future) which calls into this.
    Reload swaps the rule set atomically under self._lock; mid-
    scan reload waits.

    Construction NEVER raises on a single bad rule — instead the
    `load_errors` list carries (Path, DetectionRuleError) tuples
    so the operator gets the full punch list. The engine refuses
    to scan (returns empty result) when load_errors is non-empty;
    `ready()` is the gate the ingestor must check before wiring.
    """

    def __init__(
        self,
        rules_dir: Path | None = None,
        *,
        # Pre-loaded rules (used in tests; production goes through
        # the rules_dir path).
        rules: Iterable[DetectionRule] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._rules: tuple[DetectionRule, ...] = ()
        self.load_errors: list[tuple[Path, DetectionRuleError]] = []
        self.rules_dir = rules_dir
        if rules is not None:
            with self._lock:
                self._rules = tuple(rules)
        elif rules_dir is not None:
            self.reload_from_dir(rules_dir)

    @property
    def rules(self) -> tuple[DetectionRule, ...]:
        with self._lock:
            return self._rules

    def ready(self) -> bool:
        """True iff zero load errors. The ingestor MUST check this
        before invoking scan() — per ADR-0065 D7, a single bad
        rule blocks the entire engine."""
        return not self.load_errors

    def reload_from_dir(self, directory: Path) -> None:
        """Re-parse + atomically swap the rule set.

        Per ADR-0065 D7, if ANY rule fails to parse, the engine
        retains the previous rule set and records the failures
        in load_errors. The operator must repair the failing
        rule before the new rule set takes effect. This is
        intentional: silent fall-through to an incomplete rule
        set would mask drift.
        """
        parsed, failed = parse_rules_from_dir(directory)
        with self._lock:
            if failed:
                # Don't swap — keep the previous rule set, record
                # the failures so the operator can read them via
                # ready() / load_errors.
                self.load_errors = list(failed)
                log.warning(
                    "DetectionEngine: rule reload refused — "
                    "%d failures (previous rule set retained)",
                    len(failed),
                )
                return
            self.load_errors = []
            self._rules = tuple(parsed)
            log.info(
                "DetectionEngine: loaded %d rule(s) from %s",
                len(self._rules), directory,
            )

    def scan(
        self,
        batch_id: str,
        events: list["TelemetryEvent"],
        *,
        audit_chain: "AuditChain | None" = None,
        agent_dna: str | None = None,
    ) -> DetectionScanResult:
        """Run every active rule against every event in the batch.

        For each rule with ≥1 match, emit one `detection_fired`
        chain event carrying batch_id + matched_event_ids +
        technique + severity + rule_version + evidence. Single
        event per (rule, batch) keeps the chain growth proportional
        to firing rules, not firing events.

        audit_chain is OPTIONAL. When None, the engine still
        collects matches and returns them in the result so tests
        can inspect without standing up a chain. The ingestor's
        production wiring always passes the chain.
        """
        if not self.ready():
            return DetectionScanResult(
                batch_id=batch_id,
                rules_evaluated=0,
                events_scanned=0,
                matches_by_rule={},
                audit_event_seqs=(),
                scan_ms=0,
            )

        t0 = time.perf_counter()
        # Snapshot rules under the lock so a mid-scan reload doesn't
        # crash; the swap is atomic, but mid-iteration we want the
        # rule set the caller started with.
        with self._lock:
            rules = self._rules

        matches_by_rule: dict[str, list[DetectionMatch]] = {}
        # Outer loop is rules-first so the cheap logsource filter
        # short-circuits before we touch the payload for irrelevant
        # rules.
        for rule in rules:
            rule_matches: list[DetectionMatch] = []
            for event in events:
                if not rule.applies_to(event.source, event.event_type):
                    continue
                m = rule.evaluate(
                    event_id=event.event_id,
                    event_source=event.source,
                    event_type=event.event_type,
                    event_payload=event.payload or {},
                )
                if m is not None:
                    rule_matches.append(m)
            if rule_matches:
                matches_by_rule[rule.rule_id] = rule_matches

        scan_ms = int((time.perf_counter() - t0) * 1000)

        # Emit one chain event per (rule, batch) — collapses N
        # event matches into a single anchor with the matched_event_ids
        # list so chain growth stays bounded.
        audit_seqs: list[int] = []
        if audit_chain is not None:
            for rule_id, ms in matches_by_rule.items():
                first = ms[0]   # all matches share rule + version + technique
                try:
                    entry = audit_chain.append(
                        "detection_fired",
                        {
                            "rule_id":           rule_id,
                            "rule_version":      first.rule_version,
                            "batch_id":          batch_id,
                            "technique":         first.technique,
                            "severity":          first.level,
                            "matched_event_ids": [m.event_id for m in ms],
                            "match_count":       len(ms),
                            "fired_at":          time.time(),
                        },
                        agent_dna=agent_dna,
                    )
                    seq = getattr(entry, "seq", None)
                    if isinstance(seq, int):
                        audit_seqs.append(seq)
                except Exception as e:
                    # Chain emission failure is logged but does NOT
                    # roll back the matches — the result still
                    # carries them so callers can inspect. Same
                    # store-first/anchor-second posture as B377.
                    log.warning(
                        "DetectionEngine: chain append failed for "
                        "rule %r batch %r: %r",
                        rule_id, batch_id, e,
                    )

        # Return the inspection-friendly summary.
        return DetectionScanResult(
            batch_id=batch_id,
            rules_evaluated=len(rules),
            events_scanned=len(events),
            matches_by_rule={
                rid: [m.event_id for m in ms]
                for rid, ms in matches_by_rule.items()
            },
            audit_event_seqs=tuple(audit_seqs),
            scan_ms=scan_ms,
        )
